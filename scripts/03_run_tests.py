#!/usr/bin/env python3
"""Step 3 - Run every test case in tests/cases, write one result file per case to
reports/results/, then build one consolidated, human-readable report.

Outputs
    reports/results/<test_id>.json         full decision record + evaluation per case
    reports/consolidated_report.md         the human-readable report
    reports/summary.json                   machine-readable summary (for CI gates / dashboards)
    reports/laya_eval_dataset.jsonl        labelled cases in laya-evals format (MLOps hand-off)

Usage
    python scripts/03_run_tests.py                       # all cases
    python scripts/03_run_tests.py --pattern "PL_*"      # a subset
    python scripts/03_run_tests.py --fail-under 90       # non-zero exit if pass rate < 90%  (CI)
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from laya_credit.audit import AuditTrail  # noqa: E402
from laya_credit.config import get_settings  # noqa: E402
from laya_credit.factory import build_engine  # noqa: E402
from laya_credit.report import (build_summary, evaluate_case, find_pii,  # noqa: E402
                                render_markdown, to_eval_rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pattern", default="*.json")
    ap.add_argument("--fail-under", type=float, default=None, help="minimum pass rate in percent")
    args = ap.parse_args()

    settings = get_settings()
    cases = sorted(settings.tests_dir.glob(args.pattern))
    if not cases:
        sys.exit(f"No test cases match {settings.tests_dir / args.pattern}")
    results_dir = settings.reports_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    for old in results_dir.glob("*.json"):
        old.unlink()

    engine, conn = build_engine(settings)
    runs, audit_ids = [], []
    try:
        for path in cases:
            doc = json.loads(path.read_text(encoding="utf-8"))
            meta, request = doc["meta"], doc["request"]
            result = engine.decide(request).to_dict()
            evaluation = evaluate_case(meta, result)
            run = {"meta": meta, "questions": request["questions"], "result": result, "evaluation": evaluation}
            runs.append(run)
            audit_ids.append(result["audit"]["audit_id"])
            (results_dir / f"{meta['test_id']}.json").write_text(
                json.dumps(run, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
            mark = "PASS" if evaluation["passed"] else "FAIL"
            print(f"[{mark}] {meta['test_id']:<32} expected={meta['expected_decision']:<8} "
                  f"actual={result['final_decision']:<8} {', '.join(result['reason_codes'])}")

        audit = AuditTrail(conn)
        chain = audit.verify_chain()
        with conn.cursor() as cur:
            cur.execute("SELECT redacted_state, model_answers FROM decision_audit WHERE audit_id = ANY(%s)",
                        (audit_ids,))
            stored = list(cur.fetchall())
        pii = find_pii(stored) + find_pii([r["result"]["model_input"] for r in runs])
    finally:
        conn.close()

    first = runs[0]["result"]
    run_meta = {
        "backend": first["backend"], "backend_version": first["backend_version"],
        "checkpoint": first["checkpoint"], "laya_revision": settings.laya_revision or "UNPINNED",
        "model_registry_id": first["model_registry_id"], "min_confidence": settings.min_confidence,
        "embedding": settings.embedding_provider, "rag_top_k": settings.rag_top_k,
        "policy_versions": sorted({r["result"]["policy_version"] for r in runs}),
        "host": f"{platform.system()} {platform.machine()} / Python {platform.python_version()}",
    }
    summary = build_summary(runs, run_meta, chain, sorted(set(pii)))
    rd = settings.reports_dir
    (rd / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    (rd / "consolidated_report.md").write_text(render_markdown(summary, runs), encoding="utf-8")
    with open(rd / "laya_eval_dataset.jsonl", "w", encoding="utf-8") as fh:
        for row in to_eval_rows(runs):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    t = summary["totals"]
    rate = 100.0 * t["passed"] / t["cases"]
    print(f"\n{t['passed']}/{t['cases']} passed ({rate:.1f}%). Audit chain valid={chain['valid']}. "
          f"PII findings={len(summary['pii_leak_findings'])}.")
    print(f"Report: {rd / 'consolidated_report.md'}")
    if args.fail_under is not None and rate < args.fail_under:
        sys.exit(1)


if __name__ == "__main__":
    main()
