"""AC-04 — Node / agent outputs are validated structured objects (Pydantic) at handoff boundaries.

Verifies that each worker binds a schema, that the schemas actually reject malformed data (a
schema that accepts anything is not a contract), and that safety-critical fields are derived
structurally rather than taken on the model's word.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel, ValidationError

from discharge_copilot.nodes import workers
from discharge_copilot.schemas import (
    DischargePacket,
    DischargeSummary,
    EducationPacket,
    FollowUpAppointment,
    FollowUpPlan,
    MedicationChange,
    MedicationInteraction,
    MedicationReconciliation,
    Reflection,
    ReflectionAction,
    RiskAssessment,
    RiskTier,
    Severity,
    SupervisorPlan,
)

WORKER_SCHEMAS = {
    "summary": DischargeSummary,
    "medication": MedicationReconciliation,
    "followup": FollowUpPlan,
    "education": EducationPacket,
}


def test_ac04_every_handoff_contract_is_a_pydantic_model():
    """AC-04: all node boundary types are Pydantic models."""
    for model in (
        *WORKER_SCHEMAS.values(),
        SupervisorPlan, Reflection, RiskAssessment, DischargePacket,
    ):
        assert issubclass(model, BaseModel)


def test_ac04_each_worker_binds_its_own_schema():
    """AC-04: each worker node passes exactly one schema to `invoke_structured`."""
    source = inspect.getsource(workers)
    for worker, model in WORKER_SCHEMAS.items():
        assert f"schema={model.__name__}" in source, (
            f"worker '{worker}' does not bind {model.__name__} at its handoff"
        )
    assert ".with_structured_output" in inspect.getsource(
        __import__("discharge_copilot.llm", fromlist=["llm"])
    )


# ---------------------------------------------------------------------------
# The schemas must actually reject bad data
# ---------------------------------------------------------------------------


def test_ac04_summary_rejects_a_stub_hospital_course():
    """AC-04: a one-word hospital course is not a discharge summary."""
    with pytest.raises(ValidationError):
        DischargeSummary(
            primary_diagnosis="Pneumonia",
            hospital_course="Fine.",           # below min_length
            condition_at_discharge="Stable",
        )


def test_ac04_education_packet_requires_red_flag_symptoms():
    """AC-04: a packet with no red flags is a safety gap, and the schema refuses it."""
    with pytest.raises(ValidationError):
        EducationPacket(
            plain_language_summary="You had an infection in your lungs and are getting better.",
            red_flag_symptoms=[],
        )


def test_ac04_appointment_window_is_bounded():
    """AC-04: an appointment 'within 900 days' is a modelling error, not a plan."""
    with pytest.raises(ValidationError):
        FollowUpAppointment(specialty="Cardiology", within_days=900, reason="review")


def test_ac04_medication_action_is_a_closed_set():
    """AC-04: reconciliation actions are constrained to the four valid decisions."""
    with pytest.raises(ValidationError):
        MedicationChange(medication="Warfarin", action="maybe", reason="unsure")
    for action in ("continue", "stop", "start", "modify"):
        assert MedicationChange(
            medication="Warfarin", action=action, reason="test"
        ).action == action


def test_ac04_risk_score_is_bounded_to_a_probability():
    """AC-04: a risk score outside [0, 1] cannot enter state."""
    with pytest.raises(ValidationError):
        RiskAssessment(tier=RiskTier.HIGH, score=1.4)


def test_ac04_reflection_confidence_is_bounded():
    """AC-04: critic confidence is a probability."""
    with pytest.raises(ValidationError):
        Reflection(worker="summary", confidence=1.5, action=ReflectionAction.ACCEPT)


def test_ac04_supervisor_choice_is_a_closed_set():
    """AC-04: the supervisor cannot dispatch to a node that does not exist."""
    with pytest.raises(ValidationError):
        SupervisorPlan(next_agent="pharmacy")


# ---------------------------------------------------------------------------
# Derived safety properties — not trusted to the model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "severity,expected",
    [
        (Severity.MINOR, False),
        (Severity.MODERATE, False),
        (Severity.MAJOR, True),
        (Severity.CONTRAINDICATED, True),
    ],
)
def test_ac04_severity_determines_pharmacist_requirement(severity, expected):
    """AC-04/AC-03: `needs_pharmacist()` is derived from severity, not from a model flag."""
    recon = MedicationReconciliation(
        interactions=[
            MedicationInteraction(
                drug_a="A", drug_b="B", severity=severity, description="test"
            )
        ]
    )
    assert recon.needs_pharmacist() is expected


def test_ac04_highest_severity_is_computed_across_interactions():
    """AC-04: the escalation decision reads the worst interaction, not the first."""
    recon = MedicationReconciliation(
        interactions=[
            MedicationInteraction(drug_a="A", drug_b="B", severity=Severity.MINOR, description="x"),
            MedicationInteraction(drug_a="C", drug_b="D", severity=Severity.MAJOR, description="y"),
            MedicationInteraction(drug_a="E", drug_b="F", severity=Severity.MODERATE, description="z"),
        ]
    )
    assert recon.highest_severity == Severity.MAJOR
    assert MedicationReconciliation().highest_severity is None


def test_ac04_packet_completeness_reflects_produced_artifacts():
    """AC-04: the final packet reports partial completion honestly."""
    empty = DischargePacket(case_id="C", patient_mrn="M")
    assert empty.completeness() == 0.0
    assert empty.requires_human_approval is True

    partial = DischargePacket(
        case_id="C",
        patient_mrn="M",
        summary=DischargeSummary(
            primary_diagnosis="Pneumonia",
            hospital_course="Admitted with fever and cough, treated with antibiotics, improved.",
            condition_at_discharge="Stable",
        ),
        medications=MedicationReconciliation(),
    )
    assert partial.completeness() == 0.5


def test_ac04_post_processing_forces_pharmacist_flag(case_interactions, state_factory):
    """AC-04: a model that omits the pharmacist flag on a major interaction is corrected."""
    state = state_factory(case_interactions)
    recon = MedicationReconciliation(
        interactions=[
            MedicationInteraction(
                drug_a="Warfarin", drug_b="Fluconazole",
                severity=Severity.CONTRAINDICATED, description="marked potentiation",
            )
        ],
        pharmacist_review_required=False,
    )
    corrected = workers._post_medication(recon, state)
    assert corrected.pharmacist_review_required is True


def test_ac04_post_processing_forces_enhanced_pathway_on_high_risk(
    case_high_risk, state_factory
):
    """AC-04/AC-03: the enhanced-pathway flag is reconciled with the assessed risk tier."""
    from discharge_copilot.nodes.intake import assess_risk

    state = state_factory(case_high_risk, risk=assess_risk(case_high_risk.patient))
    plan = FollowUpPlan(
        appointments=[
            FollowUpAppointment(specialty="Cardiology", within_days=7, reason="HF review")
        ],
        enhanced_pathway=False,
    )
    assert workers._post_followup(plan, state).enhanced_pathway is True


def test_ac04_schemas_serialise_to_json_for_the_trace():
    """AC-04/NFR-04: every artifact is JSON-serialisable so it can be committed as evidence."""
    packet = DischargePacket(
        case_id="C",
        patient_mrn="M",
        risk=RiskAssessment(tier=RiskTier.HIGH, score=0.7, factors=["lives alone"]),
    )
    dumped = packet.model_dump(mode="json")
    assert dumped["risk"]["tier"] == "high"
    assert DischargePacket.model_validate(dumped).risk.tier == RiskTier.HIGH
