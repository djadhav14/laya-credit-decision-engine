"""Decision-model backends (Adapter + Strategy patterns).

``LayaBackend``  - the real thing: Laya ``Router`` running locally on this machine.
``MockBackend``  - a deterministic, keyword-based STAND-IN that returns Laya-shaped payloads.
                   It exists only so the pipeline, audit trail and reports can be exercised in
                   CI or on machines that cannot download the Laya weights. Its numbers are NOT
                   Laya inference and every report produced with it says so.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Protocol


class DecisionBackend(Protocol):
    name: str
    checkpoint: str
    version: str

    def predict(self, state: Any, questions: Dict[str, Any], min_confidence: float) -> Dict[str, Any]: ...


class LayaBackend:
    name = "laya"

    def __init__(self, checkpoint: str, revision: str | None, device: str | None,
                 max_len: int | None) -> None:
        import laya
        from laya import Router

        self.version = laya.__version__
        self.checkpoint = checkpoint
        self.max_len = max_len
        # default="english": Laya 0.4 routes undetected text to multilingual by default;
        # credit applications here are English, so we pin and record the checkpoint explicitly.
        self.router = Router(device=device, revision=revision, default="english")
        # Load every checkpoint this run can route to NOW, so a missing checkpoint fails at
        # start-up with a clear message instead of in the middle of a batch.
        needed = ["english", "multilingual"] if checkpoint == "auto" else [checkpoint]
        try:
            self.router.preload(needed)
        except Exception as exc:
            raise RuntimeError(
                f"Could not load Laya checkpoint(s) {needed} (revision={revision or 'latest'}). "
                "If HF_HUB_OFFLINE=1 is set, a checkpoint is missing from the local cache: unset it "
                "once and run  python -I -c \"from laya import Router; Router(revision='"
                f"{revision or ''}' or None).preload({needed})\"  then re-enable offline mode. "
                f"Underlying error: {type(exc).__name__}: {exc}") from exc

    def predict(self, state: Any, questions: Dict[str, Any], min_confidence: float) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {"min_confidence": min_confidence}
        if self.checkpoint != "auto":          # "auto" = let Laya's Router pick per request
            kwargs["model"] = self.checkpoint
        if self.max_len:
            kwargs["max_len"] = self.max_len
        return self.router.predict(state, questions, **kwargs)


class MockBackend:
    """Deterministic heuristic stand-in. NOT a model. See module docstring."""

    name = "mock"
    version = "mock-1.0"

    _SIGNALS: Dict[str, tuple] = {
        "purpose_prohibited": ("crypto", "f&o", "futures and options", "intraday", "betting",
                               "gambling", "casino", "buy a plot", "purchase of land", "lend it to",
                               "on-lend", "speculat"),
        "adverse_conduct": ("bounce", "returned unpaid", "gambling", "betting app", "cash deposit",
                            "unexplained cash", "dream11 losses"),
        "document_inconsistency": ("mismatch", "does not match", "different from", "not traceable",
                                   "could not be verified", "inconsistent"),
        "manipulation_attempt": ("ignore previous", "ignore all", "ignore the rules",
                                 "system prompt", "you are an ai", "approve this loan",
                                 "override", "as the ai"),
        "early_warning": ("round-trip", "round trip", "delayed by", "spike", "cheque return",
                          "returned", "not reflected in gst", "overdue"),
        "invoice_quality_issue": ("disputed", "credit note", "no e-invoice", "not in gstr-1",
                                  "related party", "group company", "sister concern"),
        "name_lender_risk": ("for my brother", "for my cousin", "on behalf", "resell", "sell it",
                             "my friend will pay", "will pay the emi"),
    }

    def __init__(self, checkpoint: str = "mock-heuristic") -> None:
        self.checkpoint = checkpoint

    # Which evidence fields each question is allowed to look at (mirrors the instructions).
    _SCOPE: Dict[str, tuple] = {
        "purpose_prohibited": ("applicant_statement",),
        "manipulation_attempt": ("applicant_statement",),
        "adverse_conduct": ("bank_statement_summary", "bureau_remarks"),
        "document_inconsistency": ("verification_notes",),
        "early_warning": ("bank_statement_summary", "verification_notes"),
        "invoice_quality_issue": ("verification_notes", "debtor_profile"),
        "name_lender_risk": ("applicant_statement", "verification_notes"),
    }

    @staticmethod
    def _text(state: Any, fields: tuple | None = None) -> str:
        if isinstance(state, dict):
            return " ".join(str(v) for k, v in state.items()
                            if k not in {"policy_excerpt", "application"}
                            and (fields is None or k in fields)).lower()
        return str(state).lower()

    # Vague wording yields an uncertain answer, so the confidence gate can be demonstrated.
    _VAGUE = ("various things", "personal requirements", "some needs", "miscellaneous")

    def _noul(self, qid: str, text: str) -> float:
        hits = sum(1 for kw in self._SIGNALS.get(qid, ()) if kw in text)
        if hits == 0 and qid == "purpose_prohibited" and any(v in text for v in self._VAGUE):
            return 0.45
        return {0: 0.08, 1: 0.78}.get(hits, 0.93)

    def predict(self, state: Any, questions: Dict[str, Any], min_confidence: float) -> Dict[str, Any]:
        t0 = time.perf_counter()
        text = self._text(state)
        flags = {qid: self._noul(qid, self._text(state, self._SCOPE.get(qid))) for qid in self._SIGNALS}
        n_flags = sum(1 for p in flags.values() if p >= 0.5)
        answers: Dict[str, Any] = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                p = flags.get(qid, 0.1)
                conf = max(p, 1 - p)
                answers[qid] = {"type": "noul", "noul": p, "confidence": conf, "answer_confidence": conf}
            elif q["type"] == "score":
                levels = len(q["criteria"])
                probs = [0.80, 0.15, 0.05] if n_flags == 0 else ([0.10, 0.75, 0.15] if n_flags == 1
                                                                   else [0.05, 0.20, 0.75])
                probs = (probs + [0.0] * levels)[:levels]
                score = sum(i * p for i, p in enumerate(probs))
                answers[qid] = {"type": "score", "score": round(score, 4),
                                "probabilities": {str(i): p for i, p in enumerate(probs)},
                                "confidence": max(probs), "answer_confidence": max(probs)}
            else:  # choice
                labels = list(q["criteria"])
                if qid == "debtor_quality":
                    pick = "weak" if any(k in text for k in ("insolvency", "disputed", "delayed by", "nclt")) \
                        else "strong" if any(k in text for k in ("listed", "psu", "government", "rated aa")) \
                        else "moderate"
                elif qid == "usage_type":
                    # includes the Hindi word for "delivery" so the multilingual test is exercised
                    pick = "livelihood" if any(k in text for k in ("delivery", "डिलीवरी", "ride-hailing",
                                                                   "gig")) \
                        else "personal" if any(k in text for k in ("commute", "office", "college", "family")) \
                        else "unclear"
                else:
                    pick = labels[0]
                probs = {lab: (0.82 if lab == pick else round(0.18 / (len(labels) - 1), 4)) for lab in labels}
                answers[qid] = {"type": "choice", "choice": pick, "probabilities": probs,
                                "confidence": 0.82, "answer_confidence": 0.82}
        for a in answers.values():
            a["abstention"] = "passed" if a["answer_confidence"] >= min_confidence else "abstained"
            a["abstention_threshold"] = min_confidence
            if a["abstention"] == "abstained":
                a["low_confidence"] = True
        return {"answers": answers,
                "routing": {"model": self.checkpoint, "reason": "mock backend - not Laya inference"},
                "usage": {"input_tokens": len(text.split()), "output_tokens": 0,
                          "latency_ms": round((time.perf_counter() - t0) * 1000, 3)}}


def build_backend(settings) -> DecisionBackend:
    if settings.backend == "laya":
        return LayaBackend(settings.laya_model, settings.laya_revision, settings.laya_device,
                           settings.laya_max_len)
    if settings.backend == "mock":
        return MockBackend()
    raise ValueError(f"Unknown DECISION_BACKEND '{settings.backend}' (use laya | mock)")
