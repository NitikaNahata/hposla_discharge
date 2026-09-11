"""Supervisor node — the plan-execute orchestrator (AC-02, §7.3).

The supervisor owns no domain output of its own. Its entire job is to decide which specialist
runs next and why. That separation is what makes the topology a supervisor pattern rather than a
monolith with helper functions.

**Pattern: plan-execute.** On its first turn the supervisor emits an ordered plan over the four
workstreams; on each subsequent turn it re-evaluates that plan against what has actually been
completed and what the critic has said. Re-planning each turn (rather than executing a frozen
list) is what lets a failed or revised worker change the ordering mid-run.

**Dependency ordering.** Two orderings are real and are enforced structurally rather than left to
the model: reconciliation precedes follow-up (the plan depends on what changed), and both precede
education (the packet must describe final decisions). The model chooses within those constraints.

**Exit conditions (NFR-07).** The supervisor is bounded by `max_supervisor_steps`. If the budget
is exhausted it routes to `finalize` with whatever has been produced, rather than looping.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import get_config
from ..llm import LLMFailure, invoke_structured
from ..schemas import SupervisorPlan
from ..state import WORKERS, DischargeState, remaining_workers

SUPERVISOR_SYSTEM = """You are the supervisor of a hospital discharge-planning copilot.

You coordinate four specialist agents. You do not do their work yourself:
  - summary     : writes the discharge summary (diagnoses, hospital course, condition)
  - medication  : reconciles pre-admission vs discharge medications, checks interactions
  - followup    : schedules follow-up appointments and home services
  - education   : writes patient-facing plain-language instructions

Your only decision each turn is which specialist runs next, or whether the case is ready to
finalize.

Hard dependency rules you must respect:
  1. 'medication' must complete before 'followup'  — the follow-up plan depends on what changed.
  2. 'medication' and 'followup' must both complete before 'education' — the patient packet must
     describe the final decisions, not provisional ones.
  3. Choose 'finalize' only when every workstream is complete, or when you are told the step
     budget is exhausted.

Prioritise by clinical urgency within those constraints. If a reviewer has raised issues about a
completed workstream, prefer re-running that specialist over starting a new one.

This is a coordination task. You never give medical advice and never decide clinical questions
yourself."""


def _fallback_plan(state: DischargeState) -> SupervisorPlan:
    """Deterministic dependency-ordered plan.

    Used when the model is unavailable or returns an unusable choice. The graph must still make
    progress when the LLM does not — that is the NFR-07 requirement, and it is why the ordering
    constraints live in code as well as in the prompt.
    """
    remaining = remaining_workers(state)
    if not remaining:
        return SupervisorPlan(
            next_agent="finalize", plan=[], rationale="All workstreams complete."
        )
    ordered = [w for w in WORKERS if w in remaining]
    return SupervisorPlan(
        next_agent=ordered[0],  # type: ignore[arg-type]
        plan=ordered,
        rationale="Deterministic dependency order applied (model unavailable or unusable).",
    )


def _legal_next(state: DischargeState) -> list[str]:
    """Workers whose dependencies are satisfied right now."""
    done = set(state.get("completed", []))
    legal: list[str] = []
    for worker in remaining_workers(state):
        if worker == "followup" and "medication" not in done:
            continue
        if worker == "education" and not {"medication", "followup"} <= done:
            continue
        legal.append(worker)
    return legal


def make_supervisor_node(tracer: Any):
    """Build the supervisor node."""
    cfg = get_config()

    def supervisor(state: DischargeState) -> dict[str, Any]:
        with tracer.node("supervisor") as carrier:
            steps = state.get("supervisor_steps", 0) + 1
            remaining = remaining_workers(state)
            legal = _legal_next(state)

            # --- Explicit exit conditions (NFR-07) ---
            if not remaining:
                tracer.emit("supervisor_decision", decision="finalize", reason="all_complete")
                carrier.update(next_agent="finalize", step=steps)
                return {"next_agent": "finalize", "supervisor_steps": steps, "plan": []}

            if steps > cfg.max_supervisor_steps:
                tracer.emit(
                    "supervisor_decision",
                    decision="finalize",
                    reason="step_budget_exhausted",
                    budget=cfg.max_supervisor_steps,
                    incomplete=remaining,
                )
                carrier.update(next_agent="finalize", step=steps, budget_exhausted=True)
                return {
                    "next_agent": "finalize",
                    "supervisor_steps": steps,
                    "escalations": [
                        f"Supervisor step budget ({cfg.max_supervisor_steps}) exhausted with "
                        f"{', '.join(remaining)} incomplete — packet finalized as partial."
                    ],
                }

            if not legal:
                # Dependencies block everything still outstanding; nothing useful remains.
                tracer.emit(
                    "supervisor_decision",
                    decision="finalize",
                    reason="no_legal_next_worker",
                    remaining=remaining,
                )
                carrier.update(next_agent="finalize", step=steps)
                return {"next_agent": "finalize", "supervisor_steps": steps}

            # --- Plan-execute: ask the model to choose among legal options ---
            open_issues = [
                f"{r.worker}: {'; '.join(r.issues)}"
                for r in state.get("reflections", [])
                if r.issues and r.action.value != "accept"
            ]
            context = (
                f"Case: {state['case_id']}\n"
                f"Primary diagnosis: {state['patient'].primary_diagnosis}\n"
                f"Readmission risk: "
                f"{state['risk'].tier.value if state.get('risk') else 'unknown'}\n"
                f"Completed: {', '.join(state.get('completed', [])) or 'none'}\n"
                f"Remaining: {', '.join(remaining)}\n"
                f"Dependency-legal choices this turn: {', '.join(legal)}\n"
                f"Step {steps} of {cfg.max_supervisor_steps}\n"
                + (
                    "Open reviewer issues:\n" + "\n".join(f"  - {i}" for i in open_issues)
                    if open_issues
                    else ""
                )
            )

            try:
                decision = invoke_structured(
                    [SystemMessage(SUPERVISOR_SYSTEM), HumanMessage(context)],
                    SupervisorPlan,
                    tracer=tracer,
                    node="supervisor",
                )
                # Guard the model's choice against the dependency rules. The prompt asks for
                # them; the code enforces them.
                if decision.next_agent not in legal and decision.next_agent != "finalize":
                    tracer.emit(
                        "supervisor_override",
                        rejected=decision.next_agent,
                        reason="dependency_violation_or_already_complete",
                        legal=legal,
                    )
                    decision = _fallback_plan(state)
            except LLMFailure as exc:
                tracer.emit(
                    "supervisor_fallback",
                    reason="llm_failure",
                    error=str(exc)[:200],
                    attempts=exc.attempts,
                )
                decision = _fallback_plan(state)

            tracer.emit(
                "supervisor_decision",
                decision=decision.next_agent,
                reason=decision.rationale,
                plan=decision.plan,
                step=steps,
            )
            carrier.update(next_agent=decision.next_agent, step=steps)
            return {
                "next_agent": decision.next_agent,
                "plan": decision.plan or remaining,
                "supervisor_steps": steps,
            }

    return supervisor
