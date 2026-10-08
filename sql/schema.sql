-- =====================================================================
-- Laya Credit Decision Engine - PostgreSQL schema (PostgreSQL 16 + pgvector)
-- Re-runnable: drops and recreates prototype objects.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DROP TABLE IF EXISTS decision_audit      CASCADE;
DROP TABLE IF EXISTS applicant           CASCADE;
DROP TABLE IF EXISTS model_registry      CASCADE;
DROP TABLE IF EXISTS laya_question       CASCADE;
DROP TABLE IF EXISTS policy_chunk        CASCADE;
DROP TABLE IF EXISTS policy_rule         CASCADE;
DROP TABLE IF EXISTS loan_product        CASCADE;

-- ---------------------------------------------------------------------
-- 1. Product master
-- ---------------------------------------------------------------------
CREATE TABLE loan_product (
    product_code     TEXT PRIMARY KEY,
    product_name     TEXT        NOT NULL,
    rate_pa          NUMERIC(5,2) NOT NULL,          -- indicative annual rate, % p.a.
    policy_version   TEXT        NOT NULL,
    policy_file      TEXT        NOT NULL,
    effective_from   DATE        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 2. Structured decision matrix (deterministic, auditable policy rules)
--    A rule FIRES (adverse) when  <parameter> <operator> <value[,value_max]> is TRUE
--    and its optional condition is TRUE.
-- ---------------------------------------------------------------------
CREATE TABLE policy_rule (
    product_code     TEXT    NOT NULL REFERENCES loan_product(product_code),
    rule_id          TEXT    NOT NULL,
    clause_ref       TEXT    NOT NULL,                -- traceability to policy clause
    parameter        TEXT    NOT NULL,
    operator         TEXT    NOT NULL CHECK (operator IN
                       ('lt','lte','gt','gte','eq','ne','between','gt_lte','gte_lt',
                        'not_between','in','not_in')),
    value            TEXT    NOT NULL,
    value_max        TEXT,
    condition        TEXT,                            -- e.g. "is_ntc eq false"
    action           TEXT    NOT NULL CHECK (action IN ('DECLINE','REFER')),
    reason_code      TEXT    NOT NULL,
    description      TEXT    NOT NULL,
    policy_version   TEXT    NOT NULL,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (product_code, rule_id, policy_version)
);

-- ---------------------------------------------------------------------
-- 3. Vectorised policy knowledge (RAG over clause-level chunks)
-- ---------------------------------------------------------------------
CREATE TABLE policy_chunk (
    chunk_id         BIGSERIAL PRIMARY KEY,
    product_code     TEXT    NOT NULL REFERENCES loan_product(product_code),
    clause_ref       TEXT    NOT NULL,
    section_title    TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    content_sha256   TEXT    NOT NULL,
    policy_version   TEXT    NOT NULL,
    embedding_model  TEXT    NOT NULL,
    embedding        vector(384) NOT NULL,
    UNIQUE (product_code, clause_ref, policy_version)
);
CREATE INDEX policy_chunk_product_idx ON policy_chunk (product_code, policy_version);
CREATE INDEX policy_chunk_hnsw_idx ON policy_chunk USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------
-- 4. Laya typed-question bank (versioned like policy)
-- ---------------------------------------------------------------------
CREATE TABLE laya_question (
    product_code     TEXT    NOT NULL REFERENCES loan_product(product_code),
    qid              TEXT    NOT NULL,
    qtype            TEXT    NOT NULL CHECK (qtype IN ('choice','score','noul')),
    instructions     TEXT    NOT NULL,
    criteria         JSONB,                           -- dict (choice) / list (score) / null (noul)
    rag_query        TEXT    NOT NULL,                -- what policy text this question needs
    refer_when       TEXT,                            -- e.g. "noul gte 0.5", "choice eq weak", "score gte 1.5"
    gate_on_low_confidence BOOLEAN NOT NULL DEFAULT TRUE, -- low-confidence answer => REFER
    reason_code      TEXT,
    clause_ref       TEXT,
    policy_version   TEXT    NOT NULL,
    PRIMARY KEY (product_code, qid, policy_version)
);

-- ---------------------------------------------------------------------
-- 5. Model registry - MLOps gate: only an APPROVED entry may serve decisions
-- ---------------------------------------------------------------------
CREATE TABLE model_registry (
    registry_id      BIGSERIAL PRIMARY KEY,
    backend          TEXT    NOT NULL,                -- laya | mock
    checkpoint       TEXT    NOT NULL,                -- english | multilingual | typed-decisions | path
    revision         TEXT,                            -- pinned HF commit SHA (recommended)
    laya_version     TEXT,
    min_confidence   NUMERIC(4,3) NOT NULL,
    status           TEXT    NOT NULL CHECK (status IN ('CANDIDATE','APPROVED','RETIRED')),
    approved_by      TEXT,
    approved_at      TIMESTAMPTZ,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 6. Synthetic applicant pool (dummy data, for batch demos)
-- ---------------------------------------------------------------------
CREATE TABLE applicant (
    applicant_id     TEXT PRIMARY KEY,
    product_code     TEXT    NOT NULL REFERENCES loan_product(product_code),
    state            JSONB   NOT NULL,                -- Laya-compatible state document
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 7. Tamper-evident decision audit trail (hash chained, append-only)
-- ---------------------------------------------------------------------
CREATE TABLE decision_audit (
    audit_id         BIGSERIAL PRIMARY KEY,
    decision_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
    decided_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    applicant_ref    TEXT        NOT NULL,
    product_code     TEXT        NOT NULL,
    policy_version   TEXT        NOT NULL,
    model_registry_id BIGINT     REFERENCES model_registry(registry_id),
    backend          TEXT        NOT NULL,
    checkpoint       TEXT        NOT NULL,
    final_decision   TEXT        NOT NULL CHECK (final_decision IN ('APPROVE','REFER','DECLINE')),
    reason_codes     TEXT[]      NOT NULL,
    rule_hits        JSONB       NOT NULL,
    model_answers    JSONB       NOT NULL,
    retrieved_clauses TEXT[]     NOT NULL,
    redacted_state   JSONB       NOT NULL,            -- PII masked before storage
    latency_ms       NUMERIC(10,2) NOT NULL,
    prev_hash        TEXT        NOT NULL,
    row_hash         TEXT        NOT NULL UNIQUE
);

-- Append-only: block UPDATE / DELETE on the audit trail
CREATE OR REPLACE FUNCTION audit_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'decision_audit is append-only (attempted %)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER decision_audit_no_update
    BEFORE UPDATE OR DELETE ON decision_audit
    FOR EACH ROW EXECUTE FUNCTION audit_is_append_only();
