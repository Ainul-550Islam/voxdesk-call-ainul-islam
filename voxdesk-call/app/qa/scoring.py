"""Deterministic score calculation.

The same rubric version and the same accepted item values always produce the
same integers. This module does not call a model.

Scores are integers. An item value is normalized to 0..10000:

    normalized = (value - min) * 10000 // (max - min)

A section score is the weighted mean of applicable items. An overall score is
the weighted mean of applicable sections. N/A items are excluded from both
numerators and denominators. A missing required item fails the review even
when the average of the other items would pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.qa.exceptions import InvalidScore

FORMULA = (
    "normalized=(value-min)*10000//(max-min); "
    "section=sum(normalized*weight)//sum(weight); "
    "overall=sum(section*section_weight)//sum(section_weight)"
)
SCALE = 10000


@dataclass(frozen=True)
class ItemInput:
    item_id: str
    section_id: str
    section_weight: int
    weight: int
    min_score: int
    max_score: int
    required: bool
    value: int | None
    not_applicable: bool


@dataclass
class ScoreResult:
    overall: int | None
    passed: bool
    missing_required: list[str] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    formula: str = FORMULA

    def as_dict(self) -> dict:
        return {
            "overall": self.overall,
            "passed": self.passed,
            "missing_required": list(self.missing_required),
            "sections": list(self.sections),
            "formula": self.formula,
        }


def _require_int(name: str, value: int, *, low: int | None = None, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidScore(f"{name} must be an integer")
    if low is not None and value < low:
        raise InvalidScore(f"{name} is below {low}")
    if high is not None and value > high:
        raise InvalidScore(f"{name} is above {high}")
    return value


def normalize(value: int, min_score: int, max_score: int) -> int:
    low = _require_int("min_score", min_score)
    high = _require_int("max_score", max_score)
    if high <= low:
        raise InvalidScore("Score range is empty")
    number = _require_int("score", value, low=low, high=high)
    return (number - low) * SCALE // (high - low)


def calculate(items: list[ItemInput], *, pass_threshold: int) -> ScoreResult:
    threshold = _require_int("pass_threshold", pass_threshold, low=0, high=SCALE)
    grouped: dict[str, list[ItemInput]] = {}
    weights: dict[str, int] = {}
    missing: list[str] = []
    for item in items:
        weight = _require_int("item_weight", item.weight, low=1)
        section_weight = _require_int("section_weight", item.section_weight, low=1)
        if item.not_applicable and item.required and item.value is None:
            # N/A is allowed only when the rubric item says so. The caller
            # already rejected a required item marked N/A when allow_na is false.
            pass
        if not item.not_applicable and item.value is None and item.required:
            missing.append(item.item_id)
        grouped.setdefault(item.section_id, []).append(item)
        weights[item.section_id] = section_weight
        if weight != item.weight:
            raise InvalidScore("Item weight changed during validation")
    sections: list[dict] = []
    overall_num = 0
    overall_den = 0
    for section_id in sorted(grouped):
        applicable = [row for row in grouped[section_id] if not row.not_applicable and row.value is not None]
        denominator = sum(row.weight for row in applicable)
        if denominator == 0:
            sections.append(
                {
                    "section_id": section_id,
                    "score": None,
                    "weight": weights[section_id],
                    "applicable": 0,
                }
            )
            continue
        numerator = 0
        for row in applicable:
            numerator += normalize(row.value, row.min_score, row.max_score) * row.weight
        section_score = numerator // denominator
        sections.append(
            {
                "section_id": section_id,
                "score": section_score,
                "weight": weights[section_id],
                "applicable": len(applicable),
            }
        )
        overall_num += section_score * weights[section_id]
        overall_den += weights[section_id]
    overall = None if overall_den == 0 else overall_num // overall_den
    passed = overall is not None and not missing and overall >= threshold
    return ScoreResult(overall=overall, passed=passed, missing_required=missing, sections=sections)


def reproduce(snapshot: dict) -> ScoreResult:
    """Recompute a stored calculation. Does not read the live scorecard."""
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        raise InvalidScore("Snapshot has no items")
    items = [
        ItemInput(
            item_id=str(row["item_id"]),
            section_id=str(row["section_id"]),
            section_weight=int(row["section_weight"]),
            weight=int(row["weight"]),
            min_score=int(row["min_score"]),
            max_score=int(row["max_score"]),
            required=bool(row["required"]),
            value=None if row.get("value") is None else int(row["value"]),
            not_applicable=bool(row.get("not_applicable")),
        )
        for row in raw_items
    ]
    return calculate(items, pass_threshold=int(snapshot["pass_threshold"]))
