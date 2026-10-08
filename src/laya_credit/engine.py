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
    def grounded_questions(self, product_code: str, pv: str,
                           bank: Dict[str, dict]) -> tuple[Dict[str, dict], List[dict]]:
        """Ground each typed question in the policy, and return the clauses used.

        Design note (learned from real Laya runs): policy text is put into the QUESTION, never
        into the evidence. Laya is an encoder that reads the state and scores options; when the
        retrieved clause sat inside the state, its own words ("bounces", "gambling", "early
        warning") were read as evidence and clean applications were flagged. So for every yes/no
        question the governing clause becomes the definition of the "true" option, and the state
        carries only applicant evidence.

        Clause selection: the question's anchor clause (exact, version-pinned lookup); if a
        question has none, the nearest clause by pgvector similarity on its ``rag_query``.
        """
        questions = self.repo.to_laya_questions(bank)
        clauses: List[dict] = []
        for qid, q in bank.items():
            clause = self.store.get_clause(product_code, pv, q["clause_ref"]) if q.get("clause_ref") else None
            if clause is None:
                hits = self.store.retrieve(product_code, pv, q["rag_query"], k=1)
                clause = hits[0] if hits else None
            if clause is None:
                continue
            clauses.append({"qid": qid, "clause_ref": clause["clause_ref"], "content": clause["content"],
                            "similarity": round(float(clause["similarity"]), 4)})
            if q["qtype"] == "noul" and q["criteria"] is None:
                definition = clause["content"].split(" ", 1)[1]          # drop the "PL-5.2" prefix
                questions[qid]["criteria"] = {
                    "false": "no evidence of this in the application",
                    "true": definition,
                }
        return questions, clauses

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

        # 2. Ground every typed question in its governing policy clause (versioned)
        #    The question set always comes from the approved bank; questions supplied in the
        #    request are ignored so a caller cannot change what the model is asked.
        questions, clauses = self.grounded_questions(product_code, pv, bank)
        t_rag = time.perf_counter()

        # 3. Laya typed decisions over the (PII-redacted) applicant evidence only
        model_state = {k: state[k] for k in NARRATIVE_FIELDS if state.get(k)}
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
            gated = abstained and bool(meta.get("gate_on_low_confidence", True)) and not flagged
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
            model_input={"state": model_state, "questions": questions})

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
