"""Deterministic policy-rule engine over the structured decision matrix.

Rules are data (rows in ``policy_rule``), not code. Evaluation never uses ``eval``: operators
are a closed whitelist, so a compromised rule row cannot execute anything.
"""
from __future__ import annotations

import operator as op
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional


def _coerce(raw: Any, like: Any) -> Any:
    """Coerce a rule's text threshold to the type of the feature it is compared with."""
    if isinstance(like, bool):
        return str(raw).strip().lower() in {"true", "1", "yes"}
    if isinstance(like, (int, float)):
        return float(raw)
    return str(raw).strip().lower()


def _norm(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


OPERATORS: Dict[str, Callable[[Any, Any, Any], bool]] = {
    "lt": lambda x, a, b: op.lt(x, a),
    "lte": lambda x, a, b: op.le(x, a),
    "gt": lambda x, a, b: op.gt(x, a),
    "gte": lambda x, a, b: op.ge(x, a),
    "eq": lambda x, a, b: op.eq(x, a),
    "ne": lambda x, a, b: op.ne(x, a),
    "between": lambda x, a, b: a <= x <= b,
    "gt_lte": lambda x, a, b: a < x <= b,
    "gte_lt": lambda x, a, b: a <= x < b,
    "not_between": lambda x, a, b: not (a <= x <= b),
    "in": lambda x, a, b: x in {s.strip().lower() for s in str(a).split("|")},
    "not_in": lambda x, a, b: x not in {s.strip().lower() for s in str(a).split("|")},
}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    clause_ref: str
    parameter: str
    operator: str
    value: str
    value_max: Optional[str]
    condition: Optional[str]
    action: str          # DECLINE | REFER
    reason_code: str
    description: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Rule":
        return cls(**{k: row[k] for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    clause_ref: str
    action: str
    reason_code: str
    description: str
    parameter: str
    observed: Any
    threshold: str

    def to_dict(self) -> dict:
        return asdict(self)


class MissingFeatureError(KeyError):
    pass


def _compare(features: Mapping[str, Any], parameter: str, operator: str,
             value: str, value_max: Optional[str]) -> bool:
    if parameter not in features or features[parameter] is None:
        raise MissingFeatureError(parameter)
    if operator not in OPERATORS:
        raise ValueError(f"Operator '{operator}' is not whitelisted")
    x = _norm(features[parameter])
    a = _coerce(value, x)
    b = _coerce(value_max, x) if value_max not in (None, "") else None
    if isinstance(x, bool):
        x = bool(x)
    elif isinstance(x, (int, float)):
        x = float(x)
    return OPERATORS[operator](x, a, b)


class RulesEngine:
    def __init__(self, rules: List[Rule]) -> None:
        self.rules = rules

    def evaluate(self, features: Mapping[str, Any]) -> List[RuleHit]:
        hits: List[RuleHit] = []
        for r in self.rules:
            if r.condition:
                p, o, v = r.condition.split(maxsplit=2)
                if not _compare(features, p, o, v, None):
                    continue
            if _compare(features, r.parameter, r.operator, r.value, r.value_max):
                threshold = f"{r.operator} {r.value}" + (f"..{r.value_max}" if r.value_max else "")
                hits.append(RuleHit(r.rule_id, r.clause_ref, r.action, r.reason_code,
                                    r.description, r.parameter, features[r.parameter], threshold))
        return hits
