"""Intake node — quarantine, risk scoring, and memory recall.

First node in the graph. Three responsibilities, all of which must happen before any LLM sees
the case:

1. **Quarantine** untrusted clinical free-text (NFR-03) — done in the CLI at case-load time and
   verified here, so no path into the graph can bypass it.
2. **Score readmission risk** deterministically. This drives the `route_risk_tier` conditional
   edge (AC-03). It is intentionally a transparent heuristic rather than an LLM call: routing
   logic that a unit test can exercise at both branches without a network round-trip is worth
   more than marginal accuracy on synthetic data.
3. **Recall cross-session memory** (AC-06, AC-07) — facts learned about this patient in earlier
   sessions are pulled into state before planning begins.
"""

from __future__ import annotations

from typing import Any

from ..context.quarantine import flag_summary, flagged_notes
from ..schemas import PatientRecord, RiskAssessment, RiskTier
from ..state import DischargeState

# Weighted, documented risk factors. Sum is clamped to [0, 1].
_RISK_WEIGHTS: list[tuple[str, float, str]] = [
    ("prior_admissions", 0.22, "readmitted within the last 12 months"),
    ("polypharmacy", 0.16, "5 or more discharge medications"),
    ("lives_alone", 0.14, "lives alone with no in-home support"),
    ("age_75_plus", 0.12, "aged 75 or over"),
    ("mobility_limited", 0.10, "limited mobility"),
    ("multimorbidity", 0.12, "three or more active problems"),
    ("high_risk_dx", 0.14, "diagnosis with a high 30-day readmission rate"),
    ("no_caregiver", 0.08, "no identified caregiver"),
    ("language_barrier", 0.06, "primary language is not English"),
]

# --- Risk-model thresholds -------------------------------------------------
# These decide the readmission tier, which drives the `route_risk_tier` conditional edge
# (AC-03). They are named and gathered here so the risk model can be audited and tuned in
# one place rather than reverse-engineered from inline comparisons.
POLYPHARMACY_MEDICATION_COUNT = 5   # discharge medications at or above this count
ADVANCED_AGE_YEARS = 75             # age at or above which age is a contributing factor
MULTIMORBIDITY_PROBLEM_COUNT = 3    # active problems or secondary diagnoses

HIGH_RISK_SCORE_THRESHOLD = 0.55    # >= this -> RiskTier.HIGH  -> enhanced follow-up path
MODERATE_RISK_SCORE_THRESHOLD = 0.30  # >= this -> RiskTier.MODERATE

# Conditions with well-documented high 30-day readmission rates (synthetic reference list).
_HIGH_RISK_DIAGNOSES = (
    "heart failure", "chf", "copd", "chronic obstructive", "pneumonia",
    "myocardial infarction", "sepsis", "cirrhosis", "stroke", "renal failure",
)


def assess_risk(patient: PatientRecord) -> RiskAssessment:
    """Deterministic readmission-risk assessment. Pure function — unit-testable (AC-03)."""
    dx = f"{patient.primary_diagnosis} {' '.join(patient.secondary_diagnoses)}".lower()

    present: dict[str, bool] = {
        "prior_admissions": patient.prior_admissions_12mo > 0,
        "polypharmacy": len(patient.discharge_medications) >= POLYPHARMACY_MEDICATION_COUNT,
        "lives_alone": patient.lives_alone,
        "age_75_plus": patient.age >= ADVANCED_AGE_YEARS,
        "mobility_limited": patient.mobility_limited,
        "multimorbidity": len(patient.problem_list) >= MULTIMORBIDITY_PROBLEM_COUNT
        or len(patient.secondary_diagnoses) >= MULTIMORBIDITY_PROBLEM_COUNT,
        "high_risk_dx": any(term in dx for term in _HIGH_RISK_DIAGNOSES),
        "no_caregiver": not patient.caregiver.strip(),
        "language_barrier": patient.primary_language.strip().lower() not in {"", "english"},
    }

    score = 0.0
    factors: list[str] = []
    for key, weight, label in _RISK_WEIGHTS:
        if present[key]:
            score += weight
            factors.append(label)

    score = round(min(score, 1.0), 3)
    if score >= HIGH_RISK_SCORE_THRESHOLD:
        tier = RiskTier.HIGH
    elif score >= MODERATE_RISK_SCORE_THRESHOLD:
        tier = RiskTier.MODERATE
    else:
        tier = RiskTier.LOW

    return RiskAssessment(
        tier=tier,
        score=score,
        factors=factors,
        rationale=(
            f"Weighted heuristic over {len(factors)} of {len(_RISK_WEIGHTS)} tracked factors "
            f"gives {score:.2f}, placing this discharge in the {tier.value} tier. "
            f"Thresholds: high >= {HIGH_RISK_SCORE_THRESHOLD}, "
            f"moderate >= {MODERATE_RISK_SCORE_THRESHOLD}."
        ),
    )


def make_intake_node(tracer: Any, memory: Any = None):
    """Build the intake node.

    `memory` is the tiered-memory facade (added in the memory phase). It is optional so the
    graph remains constructible — and testable — without a memory backend attached.
    """

    def intake(state: DischargeState) -> dict[str, Any]:
        with tracer.node("intake") as carrier:
            patient: PatientRecord = state["patient"]
            tracer.register_pii(patient.name, patient.mrn)

            notes = state.get("quarantined_notes") or []
            flagged = flagged_notes(notes)
            if flagged:
                # NFR-03 evidence: the flag is raised here, before any model call.
                tracer.emit(
                    "quarantine_flag",
                    note_count=len(notes),
                    flagged_count=len(flagged),
                    flags=flag_summary(notes),
                    action="isolated_as_data_not_instructions",
                )

            risk = assess_risk(patient)
            tracer.emit(
                "risk_assessment",
                tier=risk.tier.value,
                score=risk.score,
                factors=risk.factors,
            )

            update: dict[str, Any] = {
                "risk": risk,
                "status": "in_progress",
            }

            escalations: list[str] = []
            if flagged:
                escalations.append(
                    f"Prompt-injection patterns detected in {len(flagged)} clinical note(s); "
                    "content isolated and treated as data only."
                )

            # --- Memory recall (AC-06, AC-07) ---
            if memory is not None:
                hits = memory.recall_for_patient(
                    patient.mrn,
                    query=(
                        f"{patient.primary_diagnosis} discharge history, allergies, "
                        "caregiver, adherence, prior follow-up"
                    ),
                )
                if hits:
                    update["memory_hits"] = hits
                    tracer.memory_op(
                        "recall",
                        tier="multi",
                        count=len(hits),
                        keys=[h["key"] for h in hits],
                        cross_session=any(
                            h["session_id"] != state.get("session_id") for h in hits
                        ),
                    )

            if escalations:
                update["escalations"] = escalations

            carrier.update(
                risk_tier=risk.tier.value,
                notes_quarantined=len(notes),
                notes_flagged=len(flagged),
                memories_recalled=len(update.get("memory_hits", [])),
            )
            return update

    return intake
