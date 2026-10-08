"""Test evaluation and the consolidated, human-readable report."""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

_PII = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b|(?<!\d)[6-9][0-9]{9}(?!\d)|[\w.+-]+@[\w-]+\.[\w.]+")


def _answer_ok(ans: Dict[str, Any] | None, expected: Any) -> bool | None:
    if ans is None:
        return None
    if ans.get("type") == "noul" and isinstance(expected, bool):
        return (ans["noul"] >= 0.5) == expected
    if ans.get("type") == "choice" and isinstance(expected, str):
        return ans["choice"] == expected
    if ans.get("type") == "score" and isinstance(expected, (int, float)):
        return round(ans["score"]) == int(expected)
    return None


def evaluate_case(meta: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    answers = {s["qid"]: s["answer"] for s in result["model_signals"]}
    expected_reasons = set(meta.get("expected_reason_codes", []))
    missing = sorted(expected_reasons - set(result["reason_codes"]))
    decision_ok = result["final_decision"] == meta["expected_decision"]
    q_checks = {qid: _answer_ok(answers.get(qid), exp) for qid, exp in meta.get("expected_answers", {}).items()}
    # Governance invariants that must hold for EVERY case
    declines = any(h["action"] == "DECLINE" for h in result["rule_hits"])
    refers = any(h["action"] == "REFER" for h in result["rule_hits"])
    invariants = {
        "decline_rule_always_wins": (not declines) or result["final_decision"] == "DECLINE",
        "model_never_approves_over_rules": not ((declines or refers) and result["final_decision"] == "APPROVE"),
        "every_reason_traceable": all(r == "ALL_POLICY_CHECKS_PASSED" or r.startswith("MODEL_LOW_CONFIDENCE")
                                      or any(r == h["reason_code"] for h in result["rule_hits"])
                                      or any(r == s["reason_code"] for s in result["model_signals"])
                                      for r in result["reason_codes"]),
    }
    return {"passed": decision_ok and not missing and all(invariants.values()),
            "decision_ok": decision_ok, "missing_reason_codes": missing,
            "question_checks": q_checks, "invariants": invariants}


def _pct(a: int, b: int) -> str:
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


def _p(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def question_diagnostics(runs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per typed question: how confident the model was, how often the gate withheld it, and how
    often it raised a flag. This is the table to read first when tuning or fine-tuning."""
    acc: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"n": 0, "conf": [], "abstained": 0, "flagged": 0})
    for r in runs:
        for s in r["result"]["model_signals"]:
            d = acc[s["qid"]]
            d["n"] += 1
            c = s["answer"].get("answer_confidence")
            if c is not None:
                d["conf"].append(float(c))
            d["abstained"] += int(s["abstained"])
            d["flagged"] += int(bool(s["referred"]) and not str(s.get("reason_code") or "").startswith("MODEL_LOW"))
    return {q: {"n": d["n"], "mean_confidence": round(statistics.mean(d["conf"]), 3) if d["conf"] else None,
                "min_confidence": round(min(d["conf"]), 3) if d["conf"] else None,
                "abstained": d["abstained"], "flagged": d["flagged"]} for q, d in sorted(acc.items())}


def build_summary(runs: List[Dict[str, Any]], run_meta: Dict[str, Any], audit_check: Dict[str, Any],
                  redaction_findings: List[str], errors: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    errors = errors or []
    by_product: Dict[str, Counter] = defaultdict(Counter)
    q_total, q_ok = Counter(), Counter()
    abstained = 0
    lat = defaultdict(list)
    confusion: Dict[str, Counter] = defaultdict(Counter)
    for r in runs:
        meta, res, ev = r["meta"], r["result"], r["evaluation"]
        by_product[meta["product_code"]]["total"] += 1
        by_product[meta["product_code"]]["passed"] += int(ev["passed"])
        confusion[meta["expected_decision"]][res["final_decision"]] += 1
        for qid, ok in ev["question_checks"].items():
            if ok is not None:
                q_total[qid] += 1
                q_ok[qid] += int(ok)
        abstained += sum(1 for s in res["model_signals"] if s["abstained"])
        for k, v in res["latency_ms"].items():
            lat[k].append(v)
    for e in errors:
        by_product[e["meta"]["product_code"]]["total"] += 1
        confusion[e["meta"]["expected_decision"]]["ERROR"] += 1
    passed = sum(1 for r in runs if r["evaluation"]["passed"])
    total = len(runs) + len(errors)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": run_meta,
        "totals": {"cases": total, "passed": passed, "failed": len(runs) - passed, "errors": len(errors)},
        "errors": [{"test_id": e["meta"]["test_id"], "error": e["error"]} for e in errors],
        "question_diagnostics": question_diagnostics(runs),
        "by_product": {k: dict(v) for k, v in by_product.items()},
        "decision_confusion": {k: dict(v) for k, v in confusion.items()},
        "question_accuracy": {q: {"correct": q_ok[q], "total": q_total[q]} for q in sorted(q_total)},
        "abstentions": abstained,
        "latency_ms": {k: {"p50": round(statistics.median(v), 2), "p95": round(_p(v, 0.95), 2),
                           "max": round(max(v), 2)} for k, v in lat.items()},
        "audit_chain": audit_check,
        "pii_leak_findings": redaction_findings,
        "invariants_held": all(all(r["evaluation"]["invariants"].values()) for r in runs),
    }


def find_pii(obj: Any) -> List[str]:
    text = json.dumps(obj, ensure_ascii=False)
    return sorted(set(_PII.findall(text)))


def render_markdown(summary: Dict[str, Any], runs: List[Dict[str, Any]]) -> str:
    run, tot = summary["run"], summary["totals"]
    L: List[str] = []
    L.append("# Consolidated Test Report - Laya Credit Decision Engine\n")
    if run["backend"] == "mock":
        L.append("> **WARNING - MOCK BACKEND.** These results were produced by the deterministic keyword "
                 "stand-in (`DECISION_BACKEND=mock`), **not by Laya**. They validate the pipeline, rules, RAG, "
                 "audit trail and report only. Re-run with `DECISION_BACKEND=laya` for real model results.\n")
    L.append("## 1. Run metadata\n")
    L.append("| Item | Value |\n|---|---|")
    for k in ("backend", "backend_version", "checkpoint", "laya_revision", "model_registry_id",
              "min_confidence", "embedding", "rag_top_k", "policy_versions", "host"):
        L.append(f"| {k} | `{run.get(k)}` |")
    L.append(f"| generated_at (UTC) | `{summary['generated_at']}` |\n")

    L.append("## 2. Executive summary\n")
    L.append(f"- **{tot['passed']} of {tot['cases']} test cases passed** ({_pct(tot['passed'], tot['cases'])}); "
             f"{tot['failed']} failed, {tot['errors']} could not run.")
    for e in summary["errors"]:
        L.append(f"  - ERROR `{e['test_id']}`: {e['error']}")
    L.append(f"- Governance invariants held on every case: **{'YES' if summary['invariants_held'] else 'NO'}** "
             "(decline rules always win; the model never approves over a rule; every reason code is traceable).")
    ac = summary["audit_chain"]
    L.append(f"- Audit hash chain: **{'VALID' if ac.get('valid') else 'BROKEN'}** over {ac.get('rows')} rows.")
    L.append(f"- PII found in stored audit records for this run: **{len(summary['pii_leak_findings'])}** "
             f"{'(none)' if not summary['pii_leak_findings'] else summary['pii_leak_findings']}.")
    L.append(f"- Model answers withheld by the confidence gate (abstentions): **{summary['abstentions']}**.\n")

    L.append("## 3. Results by product\n")
    L.append("| Product | Passed | Total | Pass rate |\n|---|---:|---:|---:|")
    for p, c in sorted(summary["by_product"].items()):
        L.append(f"| {p} | {c.get('passed', 0)} | {c['total']} | {_pct(c.get('passed', 0), c['total'])} |")
    L.append("")

    L.append("## 4. Decision confusion matrix (expected rows x actual columns)\n")
    labels = ["APPROVE", "REFER", "DECLINE", "ERROR"]
    L.append("| expected \\ actual | " + " | ".join(labels) + " |\n|---|" + "---:|" * len(labels))
    for e in labels[:3]:
        row = summary["decision_confusion"].get(e, {})
        L.append(f"| **{e}** | " + " | ".join(str(row.get(a, 0)) for a in labels) + " |")
    L.append("")

    L.append("## 5. Model answer accuracy per typed question (labelled cases only)\n")
    if summary["question_accuracy"]:
        L.append("| Question | Correct | Labelled | Accuracy |\n|---|---:|---:|---:|")
        for q, c in summary["question_accuracy"].items():
            L.append(f"| `{q}` | {c['correct']} | {c['total']} | {_pct(c['correct'], c['total'])} |")
    L.append("\n*Small labelled set - indicative only. Use `reports/laya_eval_dataset.jsonl` with "
             "`laya-evals` and grow it into a fine-tuning set before drawing accuracy conclusions.*\n")

    L.append("### Model behaviour per typed question (all cases)\n")
    L.append(f"Gate threshold `answer_confidence < {summary['run'].get('min_confidence')}` = abstained. "
             "Read this before the pass rate: many abstentions mean the model is uncertain (uncalibrated or "
             "not fine-tuned for this domain), not that the engine is wrong.\n")
    L.append("| Question | Answers | Mean conf. | Min conf. | Abstained | Flagged |\n|---|---:|---:|---:|---:|---:|")
    for q, d in summary["question_diagnostics"].items():
        L.append(f"| `{q}` | {d['n']} | {d['mean_confidence']} | {d['min_confidence']} | {d['abstained']} | {d['flagged']} |")
    L.append("")

    L.append("## 6. Latency (ms per decision, this machine)\n")
    L.append("| Stage | p50 | p95 | max |\n|---|---:|---:|---:|")
    for k in ("rules", "rag", "model", "total"):
        v = summary["latency_ms"].get(k, {})
        L.append(f"| {k} | {v.get('p50')} | {v.get('p95')} | {v.get('max')} |")
    L.append("")

    L.append("## 7. Test case detail\n")
    L.append("| # | Test | Expected | Actual | Result | Reason codes |\n|---|---|---|---|---|---|")
    for i, r in enumerate(runs, 1):
        ev, res = r["evaluation"], r["result"]
        L.append(f"| {i} | `{r['meta']['test_id']}` | {r['meta']['expected_decision']} | "
                 f"{res['final_decision']} | {'PASS' if ev['passed'] else '**FAIL**'} | "
                 f"{', '.join(res['reason_codes'])} |")
    L.append("")

    fails = [r for r in runs if not r["evaluation"]["passed"]]
    L.append("## 8. Failures and observations\n")
    if not fails:
        L.append("No failures.\n")
    for r in fails:
        ev = r["evaluation"]
        L.append(f"### `{r['meta']['test_id']}`\n- {r['meta']['description']}")
        L.append(f"- Expected **{r['meta']['expected_decision']}**, got **{r['result']['final_decision']}**")
        if ev["missing_reason_codes"]:
            L.append(f"- Missing reason codes: {ev['missing_reason_codes']}")
        bad_q = [q for q, ok in ev["question_checks"].items() if ok is False]
        if bad_q:
            L.append(f"- Model answers that disagreed with the label: {bad_q}")
        L.append("")

    L.append("## 9. How to read this report\n")
    L.append("- **APPROVE** only when no decision-matrix rule fires and no model signal or abstention asks for review.")
    L.append("- **REFER** means a human credit officer decides; model flags and low-confidence answers can only ever cause REFER.")
    L.append("- **DECLINE** comes only from deterministic matrix rules, each traced to a policy clause.")
    L.append("- Per-case JSON (full rule hits, model probabilities, retrieved clauses, audit hash) is in `reports/results/`.")
    return "\n".join(L) + "\n"


def to_eval_rows(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Export labelled cases in laya-evals JSONL format ({state, questions, expected, tags})."""
    rows = []
    for r in runs:
        exp = r["meta"].get("expected_answers") or {}
        if not exp:
            continue
        state = r["result"]["model_input"]["state"]
        lang = "en" if json.dumps(state, ensure_ascii=False).isascii() else "multi"
        rows.append({"state": state, "questions": r["result"]["model_input"]["questions"], "expected": exp,
                     "tags": [r["meta"]["product_code"], r["meta"]["test_id"]], "language": lang})
    return rows
