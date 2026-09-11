"""Pydantic handoff contracts (AC-04).

Every worker agent returns one of these validated objects — never a raw string. The models are
bound to the LLM with `.with_structured_output(...)`, so a worker that cannot produce a valid
object fails loudly at its own boundary rather than passing malformed data downstream.

A `ValidationError` here is not a crash: it is caught in `nodes/base.py` and routed into the
self-healing loop (AC-12), which re-runs the worker with the validation error appended as an issue.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """Medication interaction severity. Drives the pharmacist-review edge (AC-03)."""

    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    CONTRAINDICATED = "contraindicated"

    @property
    def requires_pharmacist(self) -> bool:
        return self in {Severity.MAJOR, Severity.CONTRAINDICATED}


class RiskTier(str, Enum):
    """Readmission risk tier. Drives the enhanced-follow-up edge (AC-03)."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class ReflectionAction(str, Enum):
    """What the critic decided to do with a worker's output (AC-12)."""

    ACCEPT = "accept"
    REVISE = "revise"
    ESCALATE = "escalate"


WorkerName = Literal["summary", "medication", "followup", "education"]


# ---------------------------------------------------------------------------
# Input record (synthetic — Synthetic-Data Rule)
# ---------------------------------------------------------------------------


class Medication(BaseModel):
    """A single medication line item."""

    name: str
    dose: str = ""
    frequency: str = ""
    route: str = "oral"
    indication: str = ""

    def label(self) -> str:
        parts = [self.name, self.dose, self.frequency]
        return " ".join(p for p in parts if p).strip()


class PatientRecord(BaseModel):
    """A synthetic patient record as supplied in a pending-discharge case file."""

    mrn: str
    name: str
    age: int = Field(ge=0, le=120)
    sex: str = ""
    admission_date: date | None = None
    discharge_date: date | None = None
    primary_diagnosis: str
    secondary_diagnoses: list[str] = Field(default_factory=list)
    problem_list: list[str] = Field(default_factory=list)
    pre_admission_medications: list[Medication] = Field(default_factory=list)
    discharge_medications: list[Medication] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    prior_admissions_12mo: int = 0
    lives_alone: bool = False
    mobility_limited: bool = False
    primary_language: str = "English"
    caregiver: str = ""

    @property
    def length_of_stay_days(self) -> int | None:
        if self.admission_date and self.discharge_date:
            return (self.discharge_date - self.admission_date).days
        return None


class DischargeCase(BaseModel):
    """A pending discharge as committed under `data/samples/`."""

    case_id: str
    patient: PatientRecord
    # Free-text clinical notes are UNTRUSTED (NFR-03). They are never placed directly
    # into a prompt; `context/quarantine.py` wraps them first.
    clinical_notes: list[str] = Field(default_factory=list)
    nurse_handoff_notes: list[str] = Field(default_factory=list)
    session_id: str = "session-1"


# ---------------------------------------------------------------------------
# Intake / risk
# ---------------------------------------------------------------------------


class RiskAssessment(BaseModel):
    """Readmission-risk assessment produced at intake. Routes the follow-up path (AC-03)."""

    tier: RiskTier
    score: float = Field(ge=0.0, le=1.0)
    factors: list[str] = Field(
        default_factory=list, description="Human-readable contributing risk factors."
    )
    rationale: str = ""

    @field_validator("factors")
    @classmethod
    def _cap_factors(cls, v: list[str]) -> list[str]:
        return v[:8]


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


class SupervisorPlan(BaseModel):
    """The supervisor's plan-execute output (§7.3 pattern implementation)."""

    next_agent: Literal["summary", "medication", "followup", "education", "finalize"]
    plan: list[str] = Field(
        default_factory=list,
        description="Ordered list of remaining workstreams, most urgent first.",
    )
    rationale: str = Field(
        default="", description="Why this worker was chosen for this step."
    )


# ---------------------------------------------------------------------------
# Worker outputs — one validated contract per workstream
# ---------------------------------------------------------------------------


class DischargeSummary(BaseModel):
    """Worker output: the discharge summary."""

    primary_diagnosis: str
    secondary_diagnoses: list[str] = Field(default_factory=list)
    hospital_course: str = Field(
        min_length=40, description="Narrative of the admission, in clinical prose."
    )
    condition_at_discharge: str
    pending_results: list[str] = Field(default_factory=list)
    disposition: str = Field(
        default="home", description="e.g. home, home with services, skilled nursing facility."
    )


class MedicationChange(BaseModel):
    """One reconciliation decision about a single medication."""

    medication: str
    action: Literal["continue", "stop", "start", "modify"]
    reason: str
    prior_dose: str = ""
    new_dose: str = ""


class MedicationInteraction(BaseModel):
    """A detected interaction between two medications."""

    drug_a: str
    drug_b: str
    severity: Severity
    description: str
    recommendation: str = ""


class MedicationReconciliation(BaseModel):
    """Worker output: medication reconciliation."""

    changes: list[MedicationChange] = Field(default_factory=list)
    interactions: list[MedicationInteraction] = Field(default_factory=list)
    unreconciled: list[str] = Field(
        default_factory=list,
        description="Pre-admission medications with no explicit continue/stop decision.",
    )
    pharmacist_review_required: bool = False
    notes: str = ""

    @property
    def highest_severity(self) -> Severity | None:
        if not self.interactions:
            return None
        order = [Severity.MINOR, Severity.MODERATE, Severity.MAJOR, Severity.CONTRAINDICATED]
        return max((i.severity for i in self.interactions), key=order.index)

    def needs_pharmacist(self) -> bool:
        """Structural check — independent of what the model claimed in the boolean field."""
        return self.pharmacist_review_required or any(
            i.severity.requires_pharmacist for i in self.interactions
        )


class FollowUpAppointment(BaseModel):
    """A single scheduled follow-up."""

    specialty: str
    within_days: int = Field(ge=0, le=180)
    reason: str
    scheduled_date: str = ""
    confirmed: bool = False
    modality: Literal["in_person", "telehealth", "phone"] = "in_person"


class FollowUpPlan(BaseModel):
    """Worker output: the follow-up plan."""

    appointments: list[FollowUpAppointment] = Field(default_factory=list)
    enhanced_pathway: bool = Field(
        default=False, description="True when the high-risk enhanced follow-up track was applied."
    )
    home_services: list[str] = Field(default_factory=list)
    transport_arranged: bool = False
    notes: str = ""


class EducationPacket(BaseModel):
    """Worker output: the patient education packet."""

    plain_language_summary: str = Field(
        min_length=40, description="What happened and what to do, at roughly a 6th-grade level."
    )
    medication_instructions: list[str] = Field(default_factory=list)
    activity_and_diet: list[str] = Field(default_factory=list)
    red_flag_symptoms: list[str] = Field(
        default_factory=list, description="Symptoms that warrant immediate medical attention."
    )
    caregiver_instructions: list[str] = Field(default_factory=list)
    language: str = "English"

    @field_validator("red_flag_symptoms")
    @classmethod
    def _require_red_flags(cls, v: list[str]) -> list[str]:
        # A discharge packet with no red-flag guidance is a safety gap, not a style choice.
        if not v:
            raise ValueError("red_flag_symptoms must contain at least one symptom")
        return v


# ---------------------------------------------------------------------------
# Reflection (AC-12)
# ---------------------------------------------------------------------------


class Reflection(BaseModel):
    """The critic's judgement on one worker output."""

    worker: str
    confidence: float = Field(
        ge=0.0, le=1.0, description="Critic's confidence that the output is fit for purpose."
    )
    action: ReflectionAction
    issues: list[str] = Field(
        default_factory=list, description="Specific, actionable defects found in the output."
    )
    rationale: str = ""


# ---------------------------------------------------------------------------
# Final packet
# ---------------------------------------------------------------------------


class DischargePacket(BaseModel):
    """The assembled deliverable, produced by the finalize node."""

    case_id: str
    patient_mrn: str
    risk: RiskAssessment | None = None
    summary: DischargeSummary | None = None
    medications: MedicationReconciliation | None = None
    followup: FollowUpPlan | None = None
    education: EducationPacket | None = None
    escalations: list[str] = Field(default_factory=list)
    quarantine_flags: list[str] = Field(default_factory=list)
    requires_human_approval: bool = True

    def completeness(self) -> float:
        """Fraction of the four workstreams that produced an artifact."""
        produced = [self.summary, self.medications, self.followup, self.education]
        return sum(1 for p in produced if p is not None) / 4.0
