# Architecture, design patterns and governance mapping

## Components

```
                    ┌──────────────────────── PostgreSQL 16 + pgvector ─────────────────────────┐
                    │ loan_product  policy_rule  laya_question  policy_chunk(vector 384, HNSW)  │
                    │ model_registry   applicant   decision_audit (append-only, hash chained)   │
                    └───────▲───────────▲───────────────▲────────────────────────▲──────────────┘
                            │           │               │                        │
request {state,questions}   │           │               │                        │
   │                        │           │               │                        │
   ▼                        │           │               │                        │
DecisionEngine.decide ──► PolicyRepository   ModelRegistry.require        AuditTrail.append
   │   (rules, questions, product for the CURRENT policy version)   (APPROVED + pinned revision)
   ├─► features.build_features  → RulesEngine.evaluate  (whitelisted operators, no eval)
   ├─► PolicyStore.retrieve     (product + version filtered cosine search; anchor clause per question)
   ├─► redaction.redact         (PAN, Aadhaar, mobile, e-mail, account numbers, sensitive keys)
   ├─► DecisionBackend.predict  (LayaBackend → laya.Router  |  MockBackend for CI)
   └─► combine: DECLINE > REFER > APPROVE, per-question confidence gate → DecisionResult
```

## Design patterns used (for maintainers)

| Pattern | Where | Why |
|---|---|---|
| Rules as data | `policy_rule` table, `rules.py` | Credit policy changes are reviewed data changes, not code deployments |
| Strategy | `embeddings.py` (hashing / sentence-transformers), `backends.py` (laya / mock) | Swap implementations by configuration |
| Adapter | `LayaBackend` | Isolates the Laya API; the engine depends on a small `DecisionBackend` protocol |
| Repository | `PolicyRepository`, `PolicyStore`, `ModelRegistry`, `AuditTrail` | All SQL in one place per aggregate |
| Factory / composition root | `factory.build_engine` | One place wires config, DB, embedder, backend |
| Pure functions | `features.py`, `rules.py`, `report.evaluate_case` | Unit-testable without DB or weights |
| Fail closed | missing feature → error; unknown operator → error; unregistered model → refuse | No silent approvals |

## Governance and MLOps mapping

| Concern (RBI / DPDP / AI governance) | How the prototype addresses it | Evidence |
|---|---|---|
| No hallucinated output | Laya returns probabilities over options you define; there is no free text to invent | `model_signals[*].answer` in every result |
| Explainable adverse action | Declines come only from matrix rules carrying `clause_ref` and `reason_code` | `rule_hits`, PL-7.1 / RF-6.1 / TW-6.1 |
| Human in the loop | Model may only REFER; low-confidence answers abstain to REFER | invariant checks in every report |
| Grounding in the right policy | RAG hard-filtered to product **and** policy version; retrieved clause refs stored per decision | `retrieved_clauses` |
| Data localisation / no egress | Model, vectors and data run on your infrastructure; `HF_HUB_OFFLINE=1` after first download | `.env.example` |
| Data minimisation (DPDP) | PII redacted before inference and before audit storage | `PL_09`, report "PII findings" |
| Prompt-injection resistance | Injection is a typed question, not an instruction channel; flagged cases go to RCU | `PL_05` |
| Model change control | Registry gate: backend + checkpoint + pinned HF revision must be APPROVED | `model_registry`, `UnregisteredModelError` |
| Reproducibility | Pin `LAYA_REVISION`; the registry id is written on every audit row | `decision_audit.model_registry_id` |
| Tamper-evident audit | Append-only trigger + SHA-256 hash chain; `--verify-audit` | `decision_audit.prev_hash/row_hash` |
| Regression / eval gate | `03_run_tests.py --fail-under`, `laya-evals` dataset export, CI workflow | `.github/workflows/ci.yml` |
| Secure SDLC | bandit (SAST) + pip-audit (SCA) in CI; no `eval`; parameterised SQL only | CI |

## Known limits and next steps for a production build

1. **Fine-tune before relying on model signals.** Base checkpoints are weak zero-shot on custom typed decisions (Laya README). Collect labelled credit decisions → `laya_eval_dataset.jsonl` → fine-tune → gate with `laya-evals`.
2. **Calibrate** per question type / option count on held-out data (Laya `calibrate` utilities) before setting `LAYA_MIN_CONFIDENCE` for production.
3. **Token budget:** English checkpoint = 512 tokens. Long bank-statement analyses need summarisation upstream, `predict_long`, or the multilingual checkpoint with a larger `max_len`.
4. **Option count:** keep `choice` questions under ~20 options (Laya's documented guidance).
5. Serve via `laya-serve` behind an API gateway with `LAYA_API_KEY`, mTLS and network segmentation; ship audit rows to the SOC/SIEM.
6. Integrate real bureau, account-aggregator, GST and KYC sources; this prototype uses synthetic data only.
