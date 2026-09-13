"""Shared 0-1 score -> letter grade mapping, used by both evaluation reports."""

from __future__ import annotations

GRADE_THRESHOLDS: list[tuple[float, str]] = [
    (0.90, "A"),
    (0.80, "B"),
    (0.70, "C"),
]


def grade_for(score: float) -> str:
    """Map a 0-1 score to a letter grade using GRADE_THRESHOLDS."""
    for threshold, letter in GRADE_THRESHOLDS:
        if score >= threshold:
            return letter
    return "D"
