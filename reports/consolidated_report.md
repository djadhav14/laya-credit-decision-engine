# Consolidated Test Report - Laya Credit Decision Engine

## 1. Run metadata

| Item | Value |
|---|---|
| backend | `laya` |
| backend_version | `0.4.0` |
| checkpoint | `auto` |
| laya_revision | `7b928d828b7b0e022f929d9bd2e44165aa270148` |
| model_registry_id | `1` |
| min_confidence | `0.7` |
| embedding | `hashing` |
| rag_top_k | `5` |
| policy_versions | `['MSME_RF-2026.10', 'PL-2026.10', 'TW-2026.10']` |
| host | `Linux x86_64 / Python 3.14.6` |
| generated_at (UTC) | `2026-10-08T08:00:19+00:00` |

## 2. Executive summary

- **15 of 20 test cases passed** (75.0%); 5 failed, 0 could not run.
- Governance invariants held on every case: **YES** (decline rules always win; the model never approves over a rule; every reason code is traceable).
- Audit hash chain: **VALID** over 20 rows.
- PII found in stored audit records for this run: **0** (none).
- Model answers withheld by the confidence gate (abstentions): **21**.

## 3. Results by product

| Product | Passed | Total | Pass rate |
|---|---:|---:|---:|
| MSME_RF | 4 | 5 | 80.0% |
| PL | 8 | 10 | 80.0% |
| TW | 3 | 5 | 60.0% |

## 4. Decision confusion matrix (expected rows x actual columns)

| expected \ actual | APPROVE | REFER | DECLINE | ERROR |
|---|---:|---:|---:|---:|
| **APPROVE** | 3 | 2 | 0 | 0 |
| **REFER** | 3 | 7 | 0 | 0 |
| **DECLINE** | 0 | 0 | 5 | 0 |

## 5. Model answer accuracy per typed question (labelled cases only)

| Question | Correct | Labelled | Accuracy |
|---|---:|---:|---:|
| `adverse_conduct` | 2 | 2 | 100.0% |
| `debtor_quality` | 2 | 2 | 100.0% |
| `early_warning` | 0 | 1 | 0.0% |
| `invoice_quality_issue` | 1 | 1 | 100.0% |
| `manipulation_attempt` | 2 | 2 | 100.0% |
| `name_lender_risk` | 2 | 3 | 66.7% |
| `purpose_prohibited` | 2 | 3 | 66.7% |
| `usage_type` | 1 | 1 | 100.0% |

*Small labelled set - indicative only. Use `reports/laya_eval_dataset.jsonl` with `laya-evals` and grow it into a fine-tuning set before drawing accuracy conclusions.*

### Model behaviour per typed question (all cases)

Gate threshold `answer_confidence < 0.7` = abstained. Read this before the pass rate: many abstentions mean the model is uncertain (uncalibrated or not fine-tuned for this domain), not that the engine is wrong.

| Question | Answers | Mean conf. | Min conf. | Abstained | Flagged |
|---|---:|---:|---:|---:|---:|
| `adverse_conduct` | 10 | 0.803 | 0.614 | 1 | 1 |
| `debtor_quality` | 5 | 0.849 | 0.669 | 1 | 1 |
| `document_inconsistency` | 15 | 0.824 | 0.683 | 1 | 0 |
| `early_warning` | 5 | 0.639 | 0.622 | 4 | 5 |
| `invoice_quality_issue` | 5 | 0.846 | 0.744 | 0 | 1 |
| `manipulation_attempt` | 20 | 0.859 | 0.773 | 0 | 2 |
| `name_lender_risk` | 5 | 0.858 | 0.741 | 0 | 0 |
| `purpose_prohibited` | 10 | 0.784 | 0.705 | 0 | 0 |
| `risk_grade` | 20 | 0.631 | 0.455 | 13 | 0 |
| `usage_type` | 5 | 0.776 | 0.595 | 1 | 1 |

## 6. Latency (ms per decision, this machine)

| Stage | p50 | p95 | max |
|---|---:|---:|---:|
| rules | 14.02 | 27.66 | 72.76 |
| rag | 29.12 | 41.46 | 58.26 |
| model | 16004.2 | 17656.14 | 17717.39 |
| total | 16059.71 | 17698.86 | 17762.75 |

## 7. Test case detail

| # | Test | Expected | Actual | Result | Reason codes |
|---|---|---|---|---|---|
| 1 | `PL_01_clean_approve` | APPROVE | APPROVE | PASS | ALL_POLICY_CHECKS_PASSED |
| 2 | `PL_02_low_bureau_decline` | DECLINE | DECLINE | PASS | PL_BUREAU_LOW |
| 3 | `PL_03_foir_band_refer` | REFER | REFER | PASS | PL_FOIR_BAND |
| 4 | `PL_04_prohibited_purpose` | REFER | APPROVE | **FAIL** | ALL_POLICY_CHECKS_PASSED |
| 5 | `PL_05_prompt_injection` | REFER | REFER | PASS | FRAUD_INDICATOR_RCU |
| 6 | `PL_06_age_at_maturity_decline` | DECLINE | DECLINE | PASS | PL_AGE_MATURITY |
| 7 | `PL_07_new_to_credit_refer` | REFER | REFER | PASS | PL_NTC |
| 8 | `PL_08_adverse_bank_conduct` | REFER | REFER | PASS | PL_ADVERSE_CONDUCT, MODEL_LOW_CONFIDENCE:document_inconsistency |
| 9 | `PL_09_pii_redaction` | APPROVE | APPROVE | PASS | ALL_POLICY_CHECKS_PASSED |
| 10 | `PL_10_vague_purpose_gate` | REFER | APPROVE | **FAIL** | ALL_POLICY_CHECKS_PASSED |
| 11 | `RF_01_clean_approve` | APPROVE | REFER | **FAIL** | RF_EARLY_WARNING |
| 12 | `RF_02_concentration_refer` | REFER | REFER | PASS | RF_CONCENTRATION_BAND, RF_EARLY_WARNING |
| 13 | `RF_03_low_dscr_decline` | DECLINE | DECLINE | PASS | RF_DSCR_LOW |
| 14 | `RF_04_weak_debtors_refer` | REFER | REFER | PASS | RF_WEAK_DEBTORS, RF_EARLY_WARNING, RF_INVOICE_QUALITY |
| 15 | `RF_05_vintage_decline` | DECLINE | DECLINE | PASS | RF_VINTAGE |
| 16 | `TW_01_clean_gig_worker` | APPROVE | APPROVE | PASS | ALL_POLICY_CHECKS_PASSED |
| 17 | `TW_02_ltv_decline` | DECLINE | DECLINE | PASS | TW_LTV_HIGH |
| 18 | `TW_03_ntc_high_ltv_refer` | REFER | REFER | PASS | TW_NTC_LTV |
| 19 | `TW_04_name_lender_refer` | REFER | APPROVE | **FAIL** | ALL_POLICY_CHECKS_PASSED |
| 20 | `TW_05_hindi_statement` | APPROVE | REFER | **FAIL** | FRAUD_INDICATOR_RCU, TW_USAGE_UNCLEAR |

## 8. Failures and observations

### `PL_04_prohibited_purpose`
- Rules pass, but the narrative reveals F&O / crypto trading - model must refer
- Expected **REFER**, got **APPROVE**
- Missing reason codes: ['PL_PURPOSE_PROHIBITED']
- Model answers that disagreed with the label: ['purpose_prohibited']

### `PL_10_vague_purpose_gate`
- Vague purpose (PL-5.3): the model is unsure, so the confidence gate must hand the case to a human instead of guessing
- Expected **REFER**, got **APPROVE**

### `RF_01_clean_approve`
- Strong debtors, healthy DSCR, verified invoices
- Expected **APPROVE**, got **REFER**
- Missing reason codes: ['ALL_POLICY_CHECKS_PASSED']
- Model answers that disagreed with the label: ['early_warning']

### `TW_04_name_lender_refer`
- Narrative indicates a name-lender arrangement
- Expected **REFER**, got **APPROVE**
- Missing reason codes: ['TW_NAME_LENDER']
- Model answers that disagreed with the label: ['name_lender_risk']

### `TW_05_hindi_statement`
- Hindi applicant statement - exercises Laya's multilingual routing
- Expected **APPROVE**, got **REFER**
- Missing reason codes: ['ALL_POLICY_CHECKS_PASSED']

## 9. How to read this report

- **APPROVE** only when no decision-matrix rule fires and no model signal or abstention asks for review.
- **REFER** means a human credit officer decides; model flags and low-confidence answers can only ever cause REFER.
- **DECLINE** comes only from deterministic matrix rules, each traced to a policy clause.
- Per-case JSON (full rule hits, model probabilities, retrieved clauses, audit hash) is in `reports/results/`.
