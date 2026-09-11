"""Reflection / self-healing critic (AC-12, §7.3).

Every worker artifact passes through this node before it is accepted into state. The critic is a
separate LLM call with a separate prompt and — deliberately — no memory of *producing* the
artifact, only of judging it. A generator grading its own work tends to accept it.

The critic returns `Reflection(confidence, action, issues)` and the graph acts on `action`:

* **accept**   — the workstream is marked complete and the supervisor moves on.
* **revise**   — the worker is re-run with the critic's issues injected into its context
                 (`context/assembly.py` renders them as a "Revision required" section). Bounded by
                 `max_self_heal_retries`; on exhaustion the artifact is accepted with a recorded
                 escalation, because a mediocre draft a clinician can fix beats no draft.
* **escalate** — a defect a retry will not fix (missing source data, a safety concern). The
                 artifact is accepted and a human escalation is attached to the packet.

Two independent healing paths are evidenced in the committed traces: a low-confidence worker
output (`case_003_selfheal.jsonl`) and a hard tool failure (`case_003_faultinject.jsonl`).

Structural pre-checks run *before* the LLM critic. Some defects — a reconciliation that silently
dropped a pre-admission medication, an education packet with no red-flag symptoms — are decidable
in code. Checking them deterministically makes the critic's job narrower and its verdict more
reliable, and it means those checks hold even when the model is unavailable.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import get_config
from ..context.assembly import WORKER_CONTEXT_SPEC
from ..context.quarantine import render_quarantined
from ..llm import LLMFailure, invoke_structured
from ..schemas import (
    EducationPacket,
    FollowUpPlan,
    MedicationReconciliation,
    Reflection,
    ReflectionAction,
)
from ..state import DischargeState, worker_output

# A HIGH readmission-risk discharge must have an appointment inside this window. The
# protocol corpus and the follow-up worker's prompt both use 7 days; naming it keeps
# the critic's structural check and the worker's instruction from drifting apart.
HIGH_RISK_FOLLOWUP_WINDOW_DAYS = 7

CRITIC_SYSTEM = """You are a clinical quality reviewer inside a discharge-planning copilot. You did
not write the artifact you are reviewing. Your job is to find defects in it, not to praise it.

Judge the artifact against three questions only:
  1. GROUNDING  — is every claim supported by the source context? Invented findings, doses,
     dates or appointments are the most serious defect you can find.
  2. COMPLETENESS — does it fully cover its workstream? Silently omitted items (an unaccounted
     medication, a missing red-flag symptom) are patient-safety defects, not style issues.
  3. CONSISTENCY — does it contradict the upstream artifacts it was given?

Set confidence as your probability that this artifact is fit for a clinician to review and sign:
  >= 0.75  action 'accept'    — sound; minor wording nits do not justify a retry
  0.40-0.74 action 'revise'   — a specific, fixable defect a rewrite would correct
  <  0.40  action 'escalate'  — unsafe, ungrounded, or missing source data a retry cannot supply

Every issue you list must be specific and actionable — name the item, say what is wrong. "Could be
more detailed" is not an issue. "Metoprolol appears on the pre-admission list but in no change
record" is.

Do not invent defects to appear rigorous. A sound artifact should be accepted with an empty issue
list."""


# ---------------------------------------------------------------------------
# Deterministic structural pre-checks
# ---------------------------------------------------------------------------


def structural_issues(state: DischargeState, worker: str) -> list[str]:
    """Defects decidable in code, without a model call. Pure function — unit-testable."""
    artifact = worker_output(state, worker)
    if artifact is None:
        return [f"{worker} produced no artifact at all."]

    issues: list[str] = []
    patient = state["patient"]

    if worker == "medication" and isinstance(artifact, MedicationReconciliation):
        accounted = {c.medication.strip().lower() for c in artifact.changes}
        accounted |= {u.strip().lower() for u in artifact.unreconciled}
        for med in patient.pre_admission_medications:
            name = med.name.strip().lower()
            if name and not any(name in a or a in name for a in accounted):
                issues.append(
                    f"Pre-admission medication '{med.name}' appears in no change record and is "
                    "not listed as unreconciled — an unexplained omission."
                )
        for med in patient.discharge_medications:
            name = med.name.strip().lower()
            if name and not any(name in a or a in name for a in accounted):
                issues.append(
                    f"Discharge medication '{med.name}' appears in no change record."
                )
        for allergy in patient.allergies:
            a = allergy.strip().lower()
            if not a or a in {"nkda", "none"}:
                continue
            for med in patient.discharge_medications:
                if a in med.name.strip().lower():
                    issues.append(
                        f"Discharge medication '{med.name}' overlaps documented allergy "
                        f"'{allergy}' and was not flagged."
                    )

    if worker == "education" and isinstance(artifact, EducationPacket):
        if not artifact.red_flag_symptoms:
            issues.append("Education packet contains no red-flag symptoms.")
        if not artifact.medication_instructions and patient.discharge_medications:
            issues.append(
                "Patient has discharge medications but the packet gives no medication "
                "instructions."
            )

    if worker == "followup" and isinstance(artifact, FollowUpPlan):
        if not artifact.appointments:
            issues.append("Follow-up plan contains no appointments.")
        risk = state.get("risk")
        if risk is not None and risk.tier.value == "high":
            if not artifact.enhanced_pathway:
                issues.append(
                    "Readmission risk is HIGH but the enhanced follow-up pathway was not applied."
                )
            soon = [
                a
                for a in artifact.appointments
                if a.within_days <= HIGH_RISK_FOLLOWUP_WINDOW_DAYS
            ]
            if not soon:
                issues.append(
                    "Readmission risk is HIGH but no appointment is scheduled within 7 days."
                )

    return issues


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


def make_reflection_node(tracer: Any):
    """Build the reflection node."""
    cfg = get_config()

    def reflect(state: DischargeState) -> dict[str, Any]:
        worker = state.get("next_agent", "")
        with tracer.node("reflect", worker=worker) as carrier:
            artifact = worker_output(state, worker)
            attempts = state.get("retry_counts", {}).get(worker, 1)
            structural = structural_issues(state, worker)

            # --- Hard failure: the worker produced nothing ---
            if artifact is None:
                exhausted = attempts > cfg.max_self_heal_retries
                reflection = Reflection(
                    worker=worker,
                    confidence=0.0,
                    action=(
                        ReflectionAction.ESCALATE if exhausted else ReflectionAction.REVISE
                    ),
                    issues=structural,
                    rationale=(
                        f"No artifact produced after {attempts} attempt(s); "
                        + ("retry budget exhausted." if exhausted else "retrying.")
                    ),
                )
                tracer.emit(
                    "reflection",
                    worker=worker,
                    confidence=0.0,
                    action=reflection.action.value,
                    issues=reflection.issues,
                    source="hard_failure",
                    attempts=attempts,
                )
                carrier.update(action=reflection.action.value, confidence=0.0)
                return _apply(reflection, worker)

            # --- LLM critic ---
            payload = (
                artifact.model_dump(mode="json")
                if hasattr(artifact, "model_dump")
                else artifact
            )
            patient = state["patient"]
            source_context = (
                f"Patient (synthetic): {patient.age}y {patient.sex}, "
                f"primary diagnosis {patient.primary_diagnosis}\n"
                f"Secondary diagnoses: {', '.join(patient.secondary_diagnoses) or 'none'}\n"
                f"Admission: {patient.admission_date} · Discharge: {patient.discharge_date}\n"
                f"Pre-admission medications: "
                f"{'; '.join(m.label() for m in patient.pre_admission_medications) or 'none'}\n"
                f"Discharge medications: "
                f"{'; '.join(m.label() for m in patient.discharge_medications) or 'none'}\n"
                f"Allergies: {', '.join(patient.allergies) or 'none documented'}\n"
                f"Lives alone: {patient.lives_alone} · Mobility limited: "
                f"{patient.mobility_limited} · Caregiver: {patient.caregiver or 'none'}\n"
                f"Readmission risk: "
                f"{state['risk'].tier.value if state.get('risk') else 'unknown'}\n"
            )

            # The critic must see the SAME source the worker saw, or it will score grounded
            # detail as invention. The clinical notes are the bulk of that source, so they
            # are included here — still fenced, because the critic is no more entitled to
            # obey instructions hidden in a nurse note than the worker was (NFR-03).
            notes = render_quarantined(state.get("quarantined_notes") or [])
            if notes:
                source_context += (
                    "\nThe worker also had access to the following untrusted clinical "
                    "free-text. Detail drawn from it IS grounded — do not score it as "
                    "invented.\n" + notes + "\n"
                )

            # Upstream artifacts the worker had to stay consistent with.
            for key in WORKER_CONTEXT_SPEC.get(worker, {}).get("upstream", []):
                upstream = state.get(key)
                if upstream is not None and hasattr(upstream, "model_dump"):
                    source_context += (
                        f"\nUpstream artifact '{key}' the worker had to be consistent with:\n"
                        f"{upstream.model_dump(mode='json')}\n"
                    )
            prompt = (
                f"Workstream under review: {worker}\n"
                f"Attempt number: {attempts}\n\n"
                f"## Source context the artifact had to work from\n{source_context}\n"
                f"## Artifact to review\n{payload}\n"
            )
            if structural:
                prompt += (
                    "\n## Defects already confirmed by deterministic checks\n"
                    + "\n".join(f"- {i}" for i in structural)
                    + "\nThese are established facts. Include them in your issues and let them "
                    "lower your confidence accordingly.\n"
                )

            try:
                reflection = invoke_structured(
                    [SystemMessage(CRITIC_SYSTEM), HumanMessage(prompt)],
                    Reflection,
                    role="critic",
                    tracer=tracer,
                    node="reflect",
                )
                reflection.worker = worker
                # Structural defects are facts. The critic may not discount them.
                for issue in structural:
                    if issue not in reflection.issues:
                        reflection.issues.append(issue)
                if structural and reflection.action == ReflectionAction.ACCEPT:
                    reflection.action = ReflectionAction.REVISE
                    reflection.confidence = min(reflection.confidence, 0.6)
                    reflection.rationale += (
                        " [Overridden: deterministic structural checks found defects the "
                        "critic accepted.]"
                    )
            except LLMFailure as exc:
                # The critic is unavailable — fall back to the structural verdict alone
                # rather than blocking the run (NFR-07).
                tracer.emit(
                    "critic_fallback", worker=worker, error=str(exc)[:200]
                )
                reflection = Reflection(
                    worker=worker,
                    confidence=0.5 if structural else 0.8,
                    action=(
                        ReflectionAction.REVISE if structural else ReflectionAction.ACCEPT
                    ),
                    issues=structural,
                    rationale="Critic model unavailable; verdict from structural checks only.",
                )

            # Retry budget is a hard ceiling (NFR-07).
            if (
                reflection.action == ReflectionAction.REVISE
                and attempts > cfg.max_self_heal_retries
            ):
                reflection.action = ReflectionAction.ESCALATE
                reflection.rationale += (
                    f" [Self-heal budget of {cfg.max_self_heal_retries} exhausted; "
                    "accepting artifact with escalation for human review.]"
                )

            tracer.emit(
                "reflection",
                worker=worker,
                confidence=reflection.confidence,
                action=reflection.action.value,
                issues=reflection.issues,
                structural_issues=structural,
                attempts=attempts,
                source="llm_critic",
            )
            carrier.update(
                action=reflection.action.value, confidence=reflection.confidence
            )
            return _apply(reflection, worker)

    return reflect


def _apply(reflection: Reflection, worker: str) -> dict[str, Any]:
    """Translate the critic's verdict into a state update."""
    update: dict[str, Any] = {"reflections": [reflection]}

    if reflection.action == ReflectionAction.REVISE:
        # Self-heal: hand the issues back to the worker. Do NOT mark complete.
        update["pending_revision"] = {"worker": worker, "issues": reflection.issues}
        return update

    # accept / escalate both retire the workstream.
    update["completed"] = [worker]
    update["pending_revision"] = {}

    if reflection.action == ReflectionAction.ESCALATE:
        detail = "; ".join(reflection.issues[:3]) or reflection.rationale
        update["escalations"] = [
            f"{worker}: human review required (confidence "
            f"{reflection.confidence:.2f}) — {detail}"
        ]
    return update
