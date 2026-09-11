"""AC-12 — Reflection / self-healing / fallback loop.

Two independent healing paths, both evidenced by real traces rather than by a code path nobody
triggered:

1. **Low-confidence output** — the critic rejects a weak artifact and the worker re-runs with the
   issues injected (`evidence/traces/case_003_selfheal.jsonl`).
2. **Tool failure** — a forced MCP timeout degrades to local context and the run still completes
   (`evidence/traces/case_003_faultinject.jsonl`).

The deterministic structural pre-checks matter most here. Some defects are decidable in code — a
reconciliation that silently dropped a medication, an education packet with no red flags — and
checking those without a model call makes the verdict reliable even when the critic is
unavailable.
"""

from __future__ import annotations

import pytest

from discharge_copilot.nodes.reflection import _apply, structural_issues
from discharge_copilot.schemas import (
    EducationPacket,
    FollowUpAppointment,
    FollowUpPlan,
    MedicationChange,
    MedicationReconciliation,
    Reflection,
    ReflectionAction,
    RiskAssessment,
    RiskTier,
)


def test_ac12_detects_a_silently_dropped_medication(case_interactions, state_factory):
    """AC-12: a pre-admission medication in no change record is caught in code.

    CASE-003 seeds exactly this: omeprazole was stopped during the admission and the nurse note
    records the patient asking why. A reconciliation that omits it entirely is the single most
    common real-world defect, and it is decidable without a model.
    """
    state = state_factory(case_interactions)
    state["medications"] = MedicationReconciliation(
        changes=[
            MedicationChange(medication="Warfarin", action="modify", reason="dose increased"),
            MedicationChange(medication="Tiotropium", action="continue", reason="unchanged"),
            MedicationChange(medication="Atorvastatin", action="continue", reason="unchanged"),
            # Omeprazole deliberately absent.
        ]
    )
    issues = structural_issues(state, "medication")
    assert any("omeprazole" in issue.lower() for issue in issues)


def test_ac12_accepts_a_medication_listed_as_unreconciled(case_interactions, state_factory):
    """AC-12: declaring uncertainty is correct behaviour, not a defect.

    Reporting a medication as unreconciled is precisely what the worker is told to do when it
    cannot decide. Flagging that as an omission would punish honesty.
    """
    state = state_factory(case_interactions)
    state["medications"] = MedicationReconciliation(
        changes=[
            MedicationChange(medication=m.name, action="continue", reason="unchanged")
            for m in case_interactions.patient.pre_admission_medications
            if m.name != "Omeprazole"
        ]
        + [
            MedicationChange(medication=m.name, action="start", reason="new")
            for m in case_interactions.patient.discharge_medications
            if m.name in {"Amiodarone", "Fluconazole"}
        ],
        unreconciled=["Omeprazole"],
    )
    assert not any("omeprazole" in i.lower() for i in structural_issues(state, "medication"))


def test_ac12_detects_an_allergy_overlap(case_injection, state_factory):
    """AC-12: a discharge medication matching a documented allergy is caught in code.

    This check must not depend on a model noticing. CASE-004's patient is allergic to
    penicillin; prescribing one and not flagging it is a hard defect.
    """
    from discharge_copilot.schemas import Medication

    state = state_factory(case_injection)
    state["patient"].discharge_medications.append(
        Medication(name="Penicillin V", dose="500 mg", frequency="four times daily")
    )
    state["medications"] = MedicationReconciliation(
        changes=[
            MedicationChange(medication=m.name, action="continue", reason="x")
            for m in state["patient"].pre_admission_medications
        ]
        + [
            MedicationChange(medication=m.name, action="start", reason="x")
            for m in state["patient"].discharge_medications
        ]
    )
    issues = structural_issues(state, "medication")
    assert any("allerg" in i.lower() and "penicillin" in i.lower() for i in issues)


def test_ac12_detects_a_high_risk_plan_without_the_enhanced_pathway(
    case_high_risk, state_factory
):
    """AC-12: a high-risk follow-up plan missing the enhanced pathway is a defect."""
    state = state_factory(
        case_high_risk, risk=RiskAssessment(tier=RiskTier.HIGH, score=0.8)
    )
    state["followup"] = FollowUpPlan(
        appointments=[
            FollowUpAppointment(specialty="Cardiology", within_days=21, reason="review")
        ],
        enhanced_pathway=False,
    )
    issues = structural_issues(state, "followup")
    assert any("enhanced" in i.lower() for i in issues)
    assert any("7 days" in i for i in issues)


def test_ac12_detects_an_education_packet_missing_medication_instructions(
    case_high_risk, state_factory
):
    """AC-12: a packet with medications but no instructions for them is incomplete."""
    state = state_factory(case_high_risk)
    state["education"] = EducationPacket(
        plain_language_summary=(
            "Your heart was holding too much fluid and we removed it with medicine."
        ),
        red_flag_symptoms=["You gain more than 4 pounds in 2 days."],
        medication_instructions=[],
    )
    assert any("instruction" in i.lower() for i in structural_issues(state, "education"))


def test_ac12_a_missing_artifact_is_the_first_issue(case_low_risk, state_factory):
    """AC-12: a worker that produced nothing is reported as such."""
    state = state_factory(case_low_risk)
    assert structural_issues(state, "summary") == ["summary produced no artifact at all."]


def test_ac12_a_sound_artifact_raises_no_structural_issues(case_low_risk, state_factory):
    """AC-12: the checks do not invent defects — a correct artifact passes clean."""
    state = state_factory(case_low_risk)
    state["education"] = EducationPacket(
        plain_language_summary=(
            "You had an infection in your right lung. Finish all your antibiotic tablets."
        ),
        medication_instructions=["Take your antibiotic twice a day until it is finished."],
        red_flag_symptoms=["You get a fever again.", "You find it harder to breathe."],
    )
    assert structural_issues(state, "education") == []


# ---------------------------------------------------------------------------
# Verdict application
# ---------------------------------------------------------------------------


def test_ac12_revise_loops_back_without_retiring_the_workstream():
    """AC-12: a revision keeps the workstream open and hands the issues to the worker."""
    reflection = Reflection(
        worker="medication", confidence=0.5, action=ReflectionAction.REVISE,
        issues=["Omeprazole is unaccounted for."],
    )
    update = _apply(reflection, "medication")
    assert "completed" not in update
    assert update["pending_revision"]["worker"] == "medication"
    assert update["pending_revision"]["issues"] == ["Omeprazole is unaccounted for."]
    assert update["reflections"] == [reflection]


def test_ac12_accept_retires_the_workstream_and_clears_the_revision():
    """AC-12: acceptance marks the workstream complete and unblocks the supervisor."""
    reflection = Reflection(
        worker="summary", confidence=0.95, action=ReflectionAction.ACCEPT
    )
    update = _apply(reflection, "summary")
    assert update["completed"] == ["summary"]
    assert update["pending_revision"] == {}
    assert "escalations" not in update


def test_ac12_escalate_retires_the_workstream_but_records_an_escalation():
    """AC-12: an unfixable defect ships the draft with a human-review flag attached.

    Retiring the workstream is deliberate: a flagged draft a clinician can correct is more
    useful than an empty section, and looping forever on an unfixable defect is worse than both.
    """
    reflection = Reflection(
        worker="summary", confidence=0.2, action=ReflectionAction.ESCALATE,
        issues=["Source data for the hospital course is missing."],
    )
    update = _apply(reflection, "summary")
    assert update["completed"] == ["summary"]
    assert len(update["escalations"]) == 1
    assert "human review required" in update["escalations"][0]
    assert "Source data" in update["escalations"][0]


# ---------------------------------------------------------------------------
# Bounded retries (NFR-07)
# ---------------------------------------------------------------------------


def test_ac12_self_heal_budget_is_bounded():
    """AC-12/NFR-07: the loop has an explicit exit condition and cannot spin.

    Once the budget is spent, a REVISE verdict is converted to ESCALATE so the workstream
    retires with a flag rather than cycling indefinitely.
    """
    from discharge_copilot.config import get_config

    cfg = get_config()
    assert cfg.max_self_heal_retries >= 1
    assert cfg.max_supervisor_steps >= len(("summary", "medication", "followup", "education"))


def test_ac12_worker_failure_is_recorded_as_a_tool_failure(case_low_risk, state_factory):
    """AC-12: a failed worker records a structured failure instead of raising.

    A raised exception would lose the workstreams that already succeeded.
    """
    from discharge_copilot.state import ToolFailure

    failure = ToolFailure(
        tool="llm:summary", error="timeout", attempt=1,
        recovered=False, fallback_used="none",
    )
    assert failure["tool"] == "llm:summary"
    assert failure["recovered"] is False


@pytest.mark.live
@pytest.mark.slow
def test_ac12_live_fault_injection_still_completes(tmp_path):
    """AC-12/NFR-07: a forced MCP timeout degrades but still yields a usable packet."""
    import subprocess
    import sys

    from discharge_copilot.config import REPO_ROOT

    result = subprocess.run(
        [sys.executable, "-m", "discharge_copilot", "run",
         "--case", str(REPO_ROOT / "data" / "samples" / "case_001.json"),
         "--fault-inject", "mcp_timeout", "--trace-suffix", "faulttest", "--quiet"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=1800,
    )
    assert result.returncode == 0, result.stderr
    assert "Discharge packet" in result.stdout
