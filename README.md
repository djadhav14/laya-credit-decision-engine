# Laya Credit Decision Engine

**A governance-first credit decisioning prototype for an RBI-regulated NBFC: deterministic policy rules in PostgreSQL, vectorised credit policy (pgvector RAG), and [Laya](https://github.com/NandhaKishorM/laya) typed decisions — all running locally.**

> Prototype on dummy data. The three loan policies are fictitious and written for this demo. Not credit, legal or regulatory advice.

---

## Why this exists

Most "AI in lending" demos put a generative LLM behind a RAG pipeline and ask it to *write* a credit opinion. That creates three problems a regulated lender cannot wave away: free-text output that must be parsed and can be invented, data leaving the organisation for a hosted model, and a model whose behaviour changes when the vendor ships an update.

This prototype tests a different pattern. **Laya** — an Apache-2.0 open-weight "System 1" decision model by **Nandakishor M** (Convai Innovations) — does not generate text. It reads a state and answers *typed* questions (`choice`, `score`, `noul` yes/no) with probabilities in a single forward pass. In this engine:

| Layer | Technology | Decides |
|---|---|---|
| **Decision matrix** | PostgreSQL rows (50 rules, versioned, clause-referenced) | Hard **DECLINE** and band **REFER** on structured data (bureau, FOIR, LTV, DSCR ...) |
| **Policy knowledge** | pgvector, clause-level chunks | *Which* policy text grounds each model question |
| **Typed decisions** | Laya (local), confidence-gated | Narrative risk: prohibited purpose, adverse conduct, name-lending, prompt injection, debtor quality ... → can only **REFER** |
| **Governance** | Model registry, PII redaction, hash-chained audit trail | Who served the decision, on what evidence, provably unaltered |

**Decision hierarchy:** any DECLINE rule → **DECLINE**; else any REFER rule, model flag or low-confidence answer → **REFER** (human); else **APPROVE**. The model can add referrals. It cannot approve or decline on its own.

```
application JSON ──► features (EMI, FOIR, LTV, age@maturity) ──► decision-matrix rules ──┐
        │                                                                                ├─► DECLINE > REFER > APPROVE ─► hash-chained audit
        └─► PII redaction ─► policy RAG (pgvector, same product+version only) ─► Laya ───┘        (+ per-question confidence gate)
```

## What is in the repo

```
.
├── README.md                     ← you are here
├── docs/
│   ├── INSTALL.md                ← step-by-step installation (libraries, PostgreSQL, Laya weights)
│   ├── USAGE.md                  ← step-by-step usage + guide to the test data
│   └── architecture.md           ← design, patterns, governance / MLOps mapping, known limits
├── .env.example                  ← all configuration (copy to .env)
├── docker-compose.yml            ← PostgreSQL 16 + pgvector, bound to localhost
├── requirements.txt / requirements-ci.txt
├── sql/schema.sql                ← tables, HNSW vector index, append-only audit trigger
├── data/
│   ├── policies/                 ← 3 dummy loan policies (Markdown, clause-numbered)
│   │   ├── PL_personal_loan_policy.md
│   │   ├── MSME_RF_receivables_policy.md
│   │   └── TW_two_wheeler_policy.md
│   └── decision_matrix/          ← products.csv, policy_rules.csv (50 rules), laya_questions.json
├── scripts/
│   ├── 01_create_dummy_data.py   ← schema, matrix, policy vectors, model registry, synthetic pool, test cases
│   ├── 02_decision_engine.py     ← run decisions (file / applicant / batch) and verify the audit chain
│   └── 03_run_tests.py           ← run all tests → reports/results + consolidated report
├── src/laya_credit/              ← the engine (rules, features, RAG, backends, audit, registry, report)
├── tests/
│   ├── cases/                    ← 20 test cases in Laya request format {"state", "questions"}
│   └── unit/                     ← fast unit tests (no DB, no weights)
└── reports/
    ├── consolidated_report.md    ← human-readable report
    ├── summary.json              ← machine-readable summary (CI gate)
    ├── laya_eval_dataset.jsonl   ← labelled cases in laya-evals format
    └── results/                  ← one JSON per test case
```

## Quick start

```bash
git clone <this-repo> && cd laya-credit-decision-engine
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt                 # see docs/INSTALL.md for CPU-only torch
cp .env.example .env                            # set PG_PASSWORD
docker compose up -d                            # PostgreSQL + pgvector
python scripts/01_create_dummy_data.py --write-tests
python scripts/02_decision_engine.py --file tests/cases/PL_04_prohibited_purpose.json
python scripts/03_run_tests.py
open reports/consolidated_report.md
```

The first Laya call downloads the checkpoint from Hugging Face. After that, set `HF_HUB_OFFLINE=1` and the engine never leaves your machine.

## About the reports committed here

> **The reports in `reports/` were generated with `DECISION_BACKEND=mock`** — a deterministic keyword stand-in used where the Laya weights could not be downloaded. They prove the plumbing (rules, RAG, gating, redaction, audit chain, report) and **say "MOCK BACKEND" at the top**. The mock was written alongside these test cases, so its 20/20 is not evidence of model accuracy. **Run `python scripts/03_run_tests.py` with `DECISION_BACKEND=laya` and commit those reports** before quoting any model result.

## What the test suite demonstrates

| # | Capability | Test case(s) |
|---|---|---|
| 1 | Clause-traced hard declines from the matrix | `PL_02`, `PL_06`, `RF_03`, `RF_05`, `TW_02` |
| 2 | Band referrals (FOIR, concentration, NTC) | `PL_03`, `PL_07`, `RF_02`, `TW_03` |
| 3 | Narrative risk the rules cannot see | `PL_04` purpose, `PL_08` conduct, `RF_04` debtors, `TW_04` name-lender |
| 4 | Prompt-injection in applicant text → fraud referral, never an approval | `PL_05` |
| 5 | PII masked before the model and before the audit trail | `PL_09` |
| 6 | Confidence gate hands uncertain answers to a human | `PL_10` |
| 7 | Multilingual routing (Hindi narrative) | `TW_05` |
| 8 | Straight-through approvals | `PL_01`, `RF_01`, `TW_01` |

Every run also checks three invariants on every case — *decline rules always win; the model never approves over a rule; every reason code traces to a rule or a typed question* — and verifies the audit hash chain.

## Honest limits

- **Laya is a base to specialise, not a zero-shot underwriter.** On its own typed-decisions benchmark the project reports 0.362 accuracy for the base English checkpoint against 0.766 after fine-tuning ([Laya README](https://github.com/NandhaKishorM/laya#fine-tune-for-better-accuracy)). Expect zero-shot answers on credit narratives to need fine-tuning; that is why the model may only refer. `reports/laya_eval_dataset.jsonl` is the seed of that labelled set.
- **Shipped checkpoints are over-confident until calibrated** (per the Laya README). Fit temperatures on held-out data before trusting `LAYA_MIN_CONFIDENCE`.
- **Input window:** the English checkpoint reads 512 tokens, so narratives and RAG excerpts are kept short (`RAG_TOP_K`). Use `LAYA_MODEL=multilingual` + `LAYA_MAX_LEN` for long documents.
- The default `hashing` embedder is lexical; switch to `sentence-transformers` for semantic retrieval.
- Synthetic data only. No bureau, KYC or bank-statement integrations.

## Credits

[Laya](https://github.com/NandhaKishorM/laya) is created by **Nandakishor M** / Convai Innovations and released under Apache-2.0. This repository only uses the published `laya` package; all credit for the model belongs to its authors.

Licence: Apache-2.0.
