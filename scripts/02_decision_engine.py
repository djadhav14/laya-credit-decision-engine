#!/usr/bin/env python3
"""Step 2 - Run the Laya credit decision engine.

Usage
    python scripts/02_decision_engine.py --file tests/cases/PL_01_clean_approve.json
    python scripts/02_decision_engine.py --applicant SYN-PL-0007          # from the synthetic pool
    python scripts/02_decision_engine.py --batch PL --limit 50             # score the pool, print a summary
    python scripts/02_decision_engine.py --verify-audit                    # check the audit hash chain
    add --json to print the full decision record

Input files may be either a bare Laya request {"state":..., "questions":...} or a test case
{"meta":..., "request": {...}}.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from laya_credit.audit import AuditTrail  # noqa: E402
from laya_credit.factory import build_engine  # noqa: E402


def load_request(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc.get("request", doc)


def print_decision(res: dict) -> None:
    print(f"\n{'=' * 72}\n{res['applicant_ref']}  [{res['product_code']} / {res['policy_version']}]")
    print(f"DECISION : {res['final_decision']}")
    print(f"REASONS  : {', '.join(res['reason_codes'])}")
    for h in res["rule_hits"]:
        print(f"  rule  {h['rule_id']:<8} {h['action']:<7} {h['clause_ref']:<7} "
              f"{h['parameter']}={h['observed']} ({h['threshold']})")
    for s in res["model_signals"]:
        a = s["answer"]
        val = a.get("noul", a.get("choice", a.get("score")))
        flag = "REFER" if s["referred"] else "ok"
        print(f"  model {s['qid']:<24} {str(val):<10} conf={a.get('answer_confidence')} -> {flag}")
    print(f"  clauses: {', '.join(c['clause_ref'] for c in res['retrieved_clauses'])}")
    print(f"  backend={res['backend']} ({res['backend_version']}) routed={res['routing'].get('model')} "
          f"latency={res['latency_ms']['total']} ms  audit_id={res['audit'].get('audit_id')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", type=Path)
    src.add_argument("--applicant")
    src.add_argument("--batch", choices=["PL", "TW", "MSME_RF"])
    src.add_argument("--verify-audit", action="store_true")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--json", action="store_true", help="print the full decision record")
    ap.add_argument("--no-audit", action="store_true", help="do not write to the audit trail")
    args = ap.parse_args()

    engine, conn = build_engine()
    try:
        if args.verify_audit:
            print(json.dumps(AuditTrail(conn).verify_chain(), indent=2, default=str))
            return
        if args.batch:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM applicant WHERE product_code=%s ORDER BY applicant_id LIMIT %s",
                            (args.batch, args.limit))
                states = [r["state"] for r in cur.fetchall()]
            tally, reasons, lat = Counter(), Counter(), []
            for st in states:
                res = engine.decide({"state": st}, write_audit=not args.no_audit).to_dict()
                tally[res["final_decision"]] += 1
                reasons.update(res["reason_codes"])
                lat.append(res["latency_ms"]["total"])
            print(f"{args.batch}: {len(states)} applications  ->  {dict(tally)}")
            print("Top reasons:", ", ".join(f"{k}={v}" for k, v in reasons.most_common(8)))
            lat.sort()
            print(f"Latency ms: p50={lat[len(lat) // 2]:.1f}  p95={lat[int(len(lat) * 0.95) - 1]:.1f}")
            return
        if args.applicant:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM applicant WHERE applicant_id=%s", (args.applicant,))
                row = cur.fetchone()
            if not row:
                sys.exit(f"Applicant {args.applicant} not found")
            request = {"state": row["state"]}
        else:
            request = load_request(args.file)
        res = engine.decide(request, write_audit=not args.no_audit).to_dict()
        print(json.dumps(res, indent=2, ensure_ascii=False) if args.json else "", end="")
        if not args.json:
            print_decision(res)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
