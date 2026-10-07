"""The decision engine: deterministic policy rules + Laya typed decisions grounded in
retrieved policy clauses, with confidence gating, PII redaction and a hash-chained audit trail.

Decision hierarchy (policy PL-7.2 / RF-6.2 / TW-6.2):
    1. Any DECLINE rule hit                          -> DECLINE   (explainable, clause-traced)
    2. Any REFER rule hit, model flag, or abstention -> REFER     (human in the loop)
    3. Otherwise                                     -> APPROVE
The model can only ADD referrals. It can never approve a case on its own, nor decline one.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import psycopg

from .audit import AuditTrail
from .backends import DecisionBackend
from .config import Settings
from .features import build_features
from .policy_store import PolicyStore
from .redaction import redact
from .registry import ModelRegistry
from .rules import Rule, RulesEngine

NARRATIVE_FIELDS = ("applicant_statement", "bureau_remarks", "bank_statement_summary",
                    "verification_notes", "debtor_profile")


@dataclass
class ModelSignal:
    qid: str
    answer: Dict[str, Any]
    referred: bool
    reason_code: Optional[str]
    clause_ref: Optional[str]
    abstained: bool


@dataclass
class DecisionResult:
    applicant_ref: str
    product_code: str
    policy_version: str
    final_decision: str
    reason_codes: List[str]
    rule_hits: List[dict]
    model_signals: List[dict]
    features: Dict[str, Any]
    retrieved_clauses: List[dict]
    backend: str
    checkpoint: str
    backend_version: str
    model_registry_id: Optional[int]
    routing: Dict[str, Any]
    latency_ms: Dict[str, float]
    model_input: Dict[str, Any] = field(default_factory=dict)   # exactly what the model saw
    audit: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return json.loads(json.dumps(asdict(self), default=str))


def _check(expr: str, answer: Dict[str, Any]) -> bool:
    fld, op, val = expr.split(maxsplit=2)
    x = answer.get(fld)
    if x is None:
        return False
    if op == "eq":
        return str(x) == val
    x, v = float(x), float(val)
    return {"gte": x >= v, "gt": x > v, "lte": x <= v, "lt": x < v}[op]


class PolicyRepository:
    """Reads product, rules and question bank for the CURRENT policy version (cached)."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self._cache: Dict[str, dict] = {}

    def load(self, product_code: str) -> dict:
        if product_code in self._cache:
            return self._cache[product_code]
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM loan_product WHERE product_code=%s", (product_code,))
            product = cur.fetchone()
            if not product:
                raise ValueError(f"Unknown product_code '{product_code}'")
            pv = product["policy_version"]
            cur.execute("""SELECT * FROM policy_rule WHERE product_code=%s AND policy_version=%s
                           AND is_active ORDER BY rule_id""", (product_code, pv))
            rules = [Rule.from_row(r) for r in cur.fetchall()]
            cur.execute("""SELECT * FROM laya_question WHERE product_code=%s AND policy_version=%s
                           ORDER BY qid""", (product_code, pv))
            questions = {q["qid"]: q for q in cur.fetchall()}
        bundle = {"product": product, "rules": rules, "questions": questions}
        self._cache[product_code] = bundle
        return bundle

    @staticmethod
    def to_laya_questions(bank: Dict[str, dict]) -> Dict[str, dict]:
        out = {}
        for qid, q in bank.items():
            item = {"type": q["qtype"], "instructions": q["instructions"]}
            if q["criteria"] is not None:
                item["criteria"] = q["criteria"]
            out[qid] = item
        return out


class DecisionEngine:
    def __init__(self, conn: psycopg.Connection, settings: Settings, backend: DecisionBackend,
                 store: PolicyStore) -> None:
        self.conn = conn
        self.settings = settings
        self.backend = backend
        self.store = store
        self.repo = PolicyRepository(conn)
        self.audit = AuditTrail(conn)
        self.registry = ModelRegistry(conn)

    # ------------------------------------------------------------------ RAG
    def _policy_context(self, product_code: str, pv: str, bank: Dict[str, dict]) -> List[dict]:
        """Each question pulls its own anchor clause plus its nearest semantic neighbour,
        de-duplicated and capped at RAG_TOP_K so the state stays inside Laya's window."""
        seen, clauses = set(), []
        for q in bank.values():
            candidates = []
            if q.get("clause_ref"):
                anchor = self.store.get_clause(product_code, pv, q["clause_ref"])
                if anchor:
                    candidates.append(anchor)
            candidates += self.store.retrieve(product_code, pv, q["rag_query"], k=1)
            for c in candidates:
                if c["clause_ref"] not in seen:
                    seen.add(c["clause_ref"])
                    clauses.append({"clause_ref": c["clause_ref"], "content": c["content"],
                                    "similarity": round(float(c["similarity"]), 4)})
        priority = {q["clause_ref"] for q in bank.values() if q.get("clause_ref")}
        clauses.sort(key=lambda c: (c["clause_ref"] not in priority, -c["similarity"]))
        return clauses[: max(self.settings.rag_top_k, 1)]

    # ------------------------------------------------------------ decision
    def decide(self, request: Dict[str, Any], *, write_audit: bool = True) -> DecisionResult:
        t0 = time.perf_counter()
        state = request["state"]
        product_code = state["product_code"]
        bundle = self.repo.load(product_code)
        product, pv, bank = bundle["product"], bundle["product"]["policy_version"], bundle["questions"]

        registry_entry = None
        if self.settings.require_registered_model:
            registry_entry = self.registry.require(self.backend.name, self.backend.checkpoint,
                                                   self.settings.laya_revision)

        # 1. Deterministic rules on structured data
        features = build_features(product_code, state["application"], float(product["rate_pa"]))
        rule_hits = RulesEngine(bundle["rules"]).evaluate(features)
        t_rules = time.perf_counter()

        # 2. Ground the model in retrieved policy text
        clauses = self._policy_context(product_code, pv, bank)
        t_rag = time.perf_counter()

        # 3. Laya typed decisions over the (PII-redacted) narrative evidence
        questions = request.get("questions") or self.repo.to_laya_questions(bank)
        model_state = {k: state[k] for k in NARRATIVE_FIELDS if state.get(k)}
        model_state["policy_excerpt"] = " ".join(c["content"] for c in clauses)
        if self.settings.redact_pii:
            model_state = redact(model_state)
        raw = self.backend.predict(model_state, questions, self.settings.min_confidence)
        t_model = time.perf_counter()

        # 4. Interpret model answers under governance rules
        signals: List[ModelSignal] = []
        for qid, ans in raw.get("answers", {}).items():
            meta = bank.get(qid, {})
            abstained = bool(ans.get("low_confidence")) or ans.get("abstention") == "abstained"
            flagged = bool(meta.get("refer_when")) and _check(meta["refer_when"], ans)
            gated = abstained and bool(meta.get("refer_when"))
            signals.append(ModelSignal(qid, ans, flagged or gated,
                                       meta.get("reason_code") if flagged else
                                       (f"MODEL_LOW_CONFIDENCE:{qid}" if gated else None),
                                       meta.get("clause_ref"), abstained))

        # 5. Combine: DECLINE > REFER > APPROVE
        declines = [h for h in rule_hits if h.action == "DECLINE"]
        refers = [h for h in rule_hits if h.action == "REFER"]
        model_refers = [s for s in signals if s.referred]
        if declines:
            final = "DECLINE"
            reasons = [h.reason_code for h in declines]
        elif refers or model_refers:
            final = "REFER"
            reasons = [h.reason_code for h in refers] + [s.reason_code for s in model_refers]
        else:
            final = "APPROVE"
            reasons = ["ALL_POLICY_CHECKS_PASSED"]
        reasons = list(dict.fromkeys(reasons))  # de-dup, keep order
        t_end = time.perf_counter()

        result = DecisionResult(
            applicant_ref=state.get("applicant_ref", "UNKNOWN"), product_code=product_code,
            policy_version=pv, final_decision=final, reason_codes=reasons,
            rule_hits=[h.to_dict() for h in rule_hits], model_signals=[asdict(s) for s in signals],
            features=features, retrieved_clauses=clauses, backend=self.backend.name,
            checkpoint=self.backend.checkpoint, backend_version=self.backend.version,
            model_registry_id=registry_entry["registry_id"] if registry_entry else None,
            routing=raw.get("routing", {}),
            latency_ms={"rules": round((t_rules - t0) * 1000, 2),
                        "rag": round((t_rag - t_rules) * 1000, 2),
                        "model": round((t_model - t_rag) * 1000, 2),
                        "total": round((t_end - t0) * 1000, 2)},
            model_input=model_state)

        if write_audit:
            result.audit = self.audit.append({
                "applicant_ref": result.applicant_ref, "product_code": product_code,
                "policy_version": pv, "model_registry_id": result.model_registry_id,
                "backend": result.backend, "checkpoint": result.checkpoint,
                "final_decision": final, "reason_codes": reasons, "rule_hits": result.rule_hits,
                "model_answers": raw.get("answers", {}),
                "retrieved_clauses": [c["clause_ref"] for c in clauses],
                "redacted_state": redact(state), "latency_ms": result.latency_ms["total"]})
        return result
