"""Tamper-evident, append-only decision audit trail (hash chain over every decision)."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

import psycopg

GENESIS = "0" * 64
_HASHED_FIELDS = ("applicant_ref", "product_code", "policy_version", "model_registry_id", "backend",
                  "checkpoint", "final_decision", "reason_codes", "rule_hits", "model_answers",
                  "retrieved_clauses", "redacted_state")


def _canonical(record: Dict[str, Any]) -> str:
    # JSON round-trip first, so the hash computed at write time equals the one recomputed
    # from what PostgreSQL returns (JSONB / arrays come back as plain JSON types).
    normalised = json.loads(json.dumps({k: record[k] for k in _HASHED_FIELDS}, default=str))
    return json.dumps(normalised, sort_keys=True, separators=(",", ":"))


def _hash(prev_hash: str, record: Dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(record)).encode("utf-8")).hexdigest()


class AuditTrail:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self.conn.cursor() as cur:
            cur.execute("LOCK TABLE decision_audit IN SHARE ROW EXCLUSIVE MODE")
            cur.execute("SELECT row_hash FROM decision_audit ORDER BY audit_id DESC LIMIT 1")
            last = cur.fetchone()
            prev_hash = last["row_hash"] if last else GENESIS
            row_hash = _hash(prev_hash, record)
            cur.execute(
                """INSERT INTO decision_audit (applicant_ref, product_code, policy_version,
                       model_registry_id, backend, checkpoint, final_decision, reason_codes,
                       rule_hits, model_answers, retrieved_clauses, redacted_state, latency_ms,
                       prev_hash, row_hash)
                   VALUES (%(applicant_ref)s, %(product_code)s, %(policy_version)s,
                       %(model_registry_id)s, %(backend)s, %(checkpoint)s, %(final_decision)s,
                       %(reason_codes)s, %(rule_hits)s, %(model_answers)s, %(retrieved_clauses)s,
                       %(redacted_state)s, %(latency_ms)s, %(prev_hash)s, %(row_hash)s)
                   RETURNING audit_id, decision_id, decided_at""",
                {**record,
                 "rule_hits": json.dumps(record["rule_hits"], default=str),
                 "model_answers": json.dumps(record["model_answers"], default=str),
                 "redacted_state": json.dumps(record["redacted_state"], default=str),
                 "prev_hash": prev_hash, "row_hash": row_hash})
            out = dict(cur.fetchone())
        self.conn.commit()
        return {**out, "row_hash": row_hash, "prev_hash": prev_hash}

    def verify_chain(self) -> Dict[str, Any]:
        """Recompute every hash; any edited, inserted or removed row breaks the chain."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT audit_id, prev_hash, row_hash, applicant_ref, product_code, "
                        "policy_version, model_registry_id, backend, checkpoint, final_decision, "
                        "reason_codes, rule_hits, model_answers, retrieved_clauses, redacted_state "
                        "FROM decision_audit ORDER BY audit_id")
            rows: List[dict] = list(cur.fetchall())
        prev = GENESIS
        for r in rows:
            if r["prev_hash"] != prev or _hash(prev, r) != r["row_hash"]:
                return {"valid": False, "rows": len(rows), "broken_at_audit_id": r["audit_id"]}
            prev = r["row_hash"]
        return {"valid": True, "rows": len(rows), "head_hash": prev}
