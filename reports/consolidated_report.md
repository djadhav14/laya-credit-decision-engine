# Consolidated Test Report - Laya Credit Decision Engine

> **WARNING - MOCK BACKEND.** These results were produced by the deterministic keyword stand-in (`DECISION_BACKEND=mock`), **not by Laya**. They validate the pipeline, rules, RAG, audit trail and report only. Re-run with `DECISION_BACKEND=laya` for real model results.

## 1. Run metadata

| Item | Value |
|---|---|
| backend | `mock` |
| backend_version | `mock-1.0` |
| checkpoint | `mock-heuristic` |
| laya_revision | `UNPINNED` |
| model_registry_id | `1` |
| min_confidence | `0.7` |
| embedding | `hashing` |
| rag_top_k | `5` |
| policy_versions | `['MSME_RF-2026.10', 'PL-2026.10', 'TW-2026.10']` |
| host | `Linux x86_64 / Python 3.13.16` |
| generated_at (UTC) | `2026-10-07T16:20:15+00:00` |

## 2. Executive summary

- **20 of 20 test cases passed** (100.0%).
- Governance invariants held on every case: **YES** (decline rules always win; the model never approves over a rule; every reason code is traceable).
- Audit hash chain: **VALID** over 20 rows.
- PII found in stored audit records for this run: **0** (none).
- Model answers withheld by the confidence gate (abstentions): **1**.

## 3. Results by product

| Product | Passed | Total | Pass rate |
|---|---:|---:|---:|
| MSME_RF | 5 | 5 | 100.0% |
| PL | 10 | 10 | 100.0% |
| TW | 5 | 5 | 100.0% |

## 4. Decision confusion matrix (expected rows x actual columns)

| expected \ actual | APPROVE | REFER | DECLINE |
|---|---:|---:|---:|
| **APPROVE** | 5 | 0 | 0 |
| **REFER** | 0 | 10 | 0 |
| **DECLINE** | 0 | 0 | 5 |

## 5. Model answer accuracy per typed question (labelled cases only)

| Question | Correct | Labelled | Accuracy |
|---|---:|---:|---:|
| `adverse_conduct` | 2 | 2 | 100.0% |
| `debtor_quality` | 2 | 2 | 100.0% |
| `early_warning` | 1 | 1 | 100.0% |
| `invoice_quality_issue` | 1 | 1 | 100.0% |
| `manipulation_attempt` | 2 | 2 | 100.0% |
| `name_lender_risk` | 3 | 3 | 100.0% |
| `purpose_prohibited` | 3 | 3 | 100.0% |
| `usage_type` | 1 | 1 | 100.0% |

*Small labelled set - indicative only. Use `reports/laya_eval_dataset.jsonl` with `laya-evals` and grow it into a fine-tuning set before drawing accuracy conclusions.*

## 6. Latency (ms per decision, this machine)

| Stage | p50 | p95 | max |
|---|---:|---:|---:|
| rules | 0.47 | 1.72 | 3.54 |
| rag | 3.96 | 5.76 | 18.97 |
| model | 0.23 | 0.25 | 0.27 |
| total | 4.67 | 6.87 | 22.78 |

## 7. Test case detail

| # | Test | Expected | Actual | Result | Reason codes |
|---|---|---|---|---|---|
| 1 | `PL_01_clean_approve` | APPROVE | APPROVE | PASS | ALL_POLICY_CHECKS_PASSED |
| 2 | `PL_02_low_bureau_decline` | DECLINE | DECLINE | PASS | PL_BUREAU_LOW |
| 3 | `PL_03_foir_band_refer` | REFER | REFER | PASS | PL_FOIR_BAND |
| 4 | `PL_04_prohibited_purpose` | REFER | REFER | PASS | PL_PURPOSE_PROHIBITED |
| 5 | `PL_05_prompt_injection` | REFER | REFER | PASS | FRAUD_INDICATOR_RCU |
| 6 | `PL_06_age_at_maturity_decline` | DECLINE | DECLINE | PASS | PL_AGE_MATURITY |
| 7 | `PL_07_new_to_credit_refer` | REFER | REFER | PASS | PL_NTC |
| 8 | `PL_08_adverse_bank_conduct` | REFER | REFER | PASS | PL_ADVERSE_CONDUCT |
| 9 | `PL_09_pii_redaction` | APPROVE | APPROVE | PASS | ALL_POLICY_CHECKS_PASSED |
| 10 | `PL_10_vague_purpose_gate` | REFER | REFER | PASS | MODEL_LOW_CONFIDENCE:purpose_prohibited |
| 11 | `RF_01_clean_approve` | APPROVE | APPROVE | PASS | ALL_POLICY_CHECKS_PASSED |
| 12 | `RF_02_concentration_refer` | REFER | REFER | PASS | RF_CONCENTRATION_BAND |
| 13 | `RF_03_low_dscr_decline` | DECLINE | DECLINE | PASS | RF_DSCR_LOW |
| 14 | `RF_04_weak_debtors_refer` | REFER | REFER | PASS | RF_WEAK_DEBTORS, RF_INVOICE_QUALITY |
| 15 | `RF_05_vintage_decline` | DECLINE | DECLINE | PASS | RF_VINTAGE |
| 16 | `TW_01_clean_gig_worker` | APPROVE | APPROVE | PASS | ALL_POLICY_CHECKS_PASSED |
| 17 | `TW_02_ltv_decline` | DECLINE | DECLINE | PASS | TW_LTV_HIGH |
| 18 | `TW_03_ntc_high_ltv_refer` | REFER | REFER | PASS | TW_NTC_LTV |
| 19 | `TW_04_name_lender_refer` | REFER | REFER | PASS | TW_NAME_LENDER, TW_USAGE_UNCLEAR |
| 20 | `TW_05_hindi_statement` | APPROVE | APPROVE | PASS | ALL_POLICY_CHECKS_PASSED |

## 8. Failures and observations

No failures.

## 9. How to read this report

- **APPROVE** only when no decision-matrix rule fires and no model signal or abstention asks for review.
- **REFER** means a human credit officer decides; model flags and low-confidence answers can only ever cause REFER.
- **DECLINE** comes only from deterministic matrix rules, each traced to a policy clause.
- Per-case JSON (full rule hits, model probabilities, retrieved clauses, audit hash) is in `reports/results/`.
