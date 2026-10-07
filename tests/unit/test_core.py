"""Fast unit tests - no database, no model weights. Run: pytest -q tests/unit"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest  # noqa: E402

from laya_credit.backends import MockBackend  # noqa: E402
from laya_credit.engine import _check  # noqa: E402
from laya_credit.features import emi, pl_features, tw_features  # noqa: E402
from laya_credit.redaction import redact  # noqa: E402
from laya_credit.rules import MissingFeatureError, Rule, RulesEngine  # noqa: E402


def _rule(**kw):
    base = dict(rule_id="R1", clause_ref="X-1.1", parameter="foir", operator="gt", value="0.55",
                value_max=None, condition=None, action="DECLINE", reason_code="FOIR", description="d")
    base.update(kw)
    return Rule(**base)


def test_emi_matches_annuity_formula():
    assert emi(400000, 16, 36) == pytest.approx(14062.8, rel=1e-3)


def test_rule_fires_and_band_operators():
    eng = RulesEngine([_rule(), _rule(rule_id="R2", operator="gt_lte", value="0.50", value_max="0.55",
                                      action="REFER", reason_code="BAND")])
    assert [h.reason_code for h in eng.evaluate({"foir": 0.60})] == ["FOIR"]
    assert [h.reason_code for h in eng.evaluate({"foir": 0.52})] == ["BAND"]
    assert eng.evaluate({"foir": 0.50}) == []


def test_condition_gates_rule():
    eng = RulesEngine([_rule(parameter="bureau_score", operator="lt", value="650", condition="is_ntc eq false")])
    assert eng.evaluate({"bureau_score": -1, "is_ntc": True}) == []
    assert len(eng.evaluate({"bureau_score": 600, "is_ntc": False})) == 1


def test_missing_feature_fails_closed():
    with pytest.raises(MissingFeatureError):
        RulesEngine([_rule()]).evaluate({})


def test_unknown_operator_rejected():
    with pytest.raises(ValueError):
        RulesEngine([_rule(operator="__import__")]).evaluate({"foir": 1})


def test_pl_features_age_at_maturity_and_ntc():
    f = pl_features({"application_date": "2026-10-05", "dob": "1968-06-01", "employment_type": "salaried",
                     "net_monthly_income": 72000, "current_employment_months": 30,
                     "total_experience_months": 100, "loan_amount": 400000, "tenure_months": 36,
                     "bureau_score": None}, 16.0)
    assert f["age_at_maturity"] > 60 and f["is_ntc"] is True


def test_tw_ltv():
    f = tw_features({"application_date": "2026-10-05", "dob": "1997-01-01", "net_monthly_income": 28000,
                     "loan_amount": 114000, "on_road_price": 120000, "tenure_months": 36,
                     "bureau_score": 700, "residence_months": 20, "dealer_empanelled": True}, 13.0)
    assert f["ltv"] == pytest.approx(0.95)


def test_redaction_masks_indian_identifiers():
    out = redact({"pan": "ABCPK1234F", "note": "PAN ABCPK1234F, call 9876543210, mail a.b@example.com"})
    assert out["pan"] == "[REDACTED]"
    assert "ABCPK1234F" not in out["note"] and "9876543210" not in out["note"] and "@" not in out["note"]


def test_refer_when_expressions():
    assert _check("noul gte 0.5", {"noul": 0.7})
    assert not _check("noul gte 0.5", {"noul": 0.2})
    assert _check("choice eq weak", {"choice": "weak"})
    assert _check("score gte 1.5", {"score": 1.6})


def test_mock_backend_returns_laya_shape_and_gates():
    q = {"purpose_prohibited": {"type": "noul", "instructions": "?"}}
    out = MockBackend().predict({"applicant_statement": "money for crypto and f&o"}, q, 0.7)
    a = out["answers"]["purpose_prohibited"]
    assert a["type"] == "noul" and a["noul"] >= 0.5 and a["abstention"] == "passed"
    vague = MockBackend().predict({"applicant_statement": "personal requirements"}, q, 0.7)
    assert vague["answers"]["purpose_prohibited"]["low_confidence"] is True
