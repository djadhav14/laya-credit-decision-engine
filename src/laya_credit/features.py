"""Feature derivation: turns a raw application ``state`` into the numeric features the
decision matrix reads (EMI, FOIR, LTV, age at maturity ...). Pure functions, unit-testable."""
from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, Mapping


def emi(principal: float, annual_rate_pct: float, months: int) -> float:
    r = annual_rate_pct / 1200.0
    if r == 0:
        return principal / months
    return principal * r * (1 + r) ** months / ((1 + r) ** months - 1)


def age_on(dob: str, on: str) -> float:
    d, o = date.fromisoformat(dob), date.fromisoformat(on)
    return round((o - d).days / 365.25, 2)


def _retail_common(app: Mapping[str, Any], rate_pa: float) -> Dict[str, Any]:
    amount, tenure = float(app["loan_amount"]), int(app["tenure_months"])
    income = float(app["net_monthly_income"])
    new_emi = emi(amount, rate_pa, tenure)
    age = age_on(app["dob"], app["application_date"])
    bureau = app.get("bureau_score")
    return {
        "age": age,
        "age_at_maturity": round(age + tenure / 12.0, 2),
        "net_monthly_income": income,
        "loan_amount": amount,
        "tenure_months": tenure,
        "proposed_emi": round(new_emi, 2),
        "foir": round((float(app.get("existing_emi", 0)) + new_emi) / income, 4),
        "is_ntc": bureau in (None, -1),
        "bureau_score": -1 if bureau is None else int(bureau),
        "max_dpd_12m": int(app.get("max_dpd_12m", 0)),
    }


def pl_features(app: Mapping[str, Any], rate_pa: float) -> Dict[str, Any]:
    f = _retail_common(app, rate_pa)
    f.update({
        "employment_type": app["employment_type"],
        "current_employment_months": int(app["current_employment_months"]),
        "total_experience_months": int(app["total_experience_months"]),
        "writeoff_settled_24m": bool(app.get("writeoff_settled_24m", False)),
        "enquiries_6m": int(app.get("enquiries_6m", 0)),
    })
    return f


def tw_features(app: Mapping[str, Any], rate_pa: float) -> Dict[str, Any]:
    f = _retail_common(app, rate_pa)
    f.update({
        "ltv": round(float(app["loan_amount"]) / float(app["on_road_price"]), 4),
        "residence_months": int(app["residence_months"]),
        "owns_residence": bool(app.get("owns_residence", False)),
        "dealer_empanelled": bool(app["dealer_empanelled"]),
        "vehicle_condition": app.get("vehicle_condition", "new"),
    })
    return f


def msme_features(app: Mapping[str, Any], rate_pa: float) -> Dict[str, Any]:
    eligible = float(app["eligible_receivables"])
    return {
        "udyam_registered": bool(app["udyam_registered"]),
        "gst_registered": bool(app["gst_registered"]),
        "business_vintage_months": int(app["business_vintage_months"]),
        "annual_turnover": float(app["annual_turnover"]),
        "gst_filing_gaps_6m": int(app.get("gst_filing_gaps_6m", 0)),
        "promoter_bureau_score": int(app["promoter_bureau_score"]),
        "cmr_rank": int(app["cmr_rank"]),
        "dscr": float(app["dscr"]),
        "sma2_npa_12m": bool(app.get("sma2_npa_12m", False)),
        "receivables_over_90d_pct": float(app.get("receivables_over_90d_pct", 0)),
        "facility_amount": float(app["facility_amount"]),
        "advance_rate": round(float(app["facility_amount"]) / eligible, 4) if eligible else 9.99,
        "top_debtor_concentration": float(app["top_debtor_concentration"]),
        "related_party_receivables_pct": float(app.get("related_party_receivables_pct", 0)),
    }


FEATURE_BUILDERS: Dict[str, Callable[[Mapping[str, Any], float], Dict[str, Any]]] = {
    "PL": pl_features,
    "TW": tw_features,
    "MSME_RF": msme_features,
}


def build_features(product_code: str, application: Mapping[str, Any], rate_pa: float) -> Dict[str, Any]:
    try:
        return FEATURE_BUILDERS[product_code](application, rate_pa)
    except KeyError as exc:
        raise ValueError(f"Application is missing field {exc} for product {product_code}") from exc
