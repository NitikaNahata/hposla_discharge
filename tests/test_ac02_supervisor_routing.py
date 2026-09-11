"""AC-02 — A supervisor routes a pending discharge to specialized worker agents.

Covers the topology (a supervisor node dispatching to four named specialists), the dependency
ordering the supervisor must respect, and the deterministic fallback that keeps the graph making
progress when the model is unavailable.
"""

from __future__ import annotations

import pytest

from discharge_copilot.graph import build_graph, make_checkpointer
from discharge_copilot.nodes.supervisor import _fallback_plan, _legal_next
from discharge_copilot.state import WORKERS


def test_ac02_four_specialist_workers_are_declared():
    """AC-02: exactly the four required workstreams exist."""
    assert set(WORKERS) == {"summary", "medication", "followup", "education"}


def test_ac02_graph_contains_supervisor_and_all_workers(tmp_tracer, tmp_path):
    """AC-02: the compiled graph has a supervisor node plus one node per specialist."""
    graph = build_graph(tmp_tracer, checkpointer=make_checkpointer(tmp_path / "cp.sqlite"))
    nodes = set(graph.get_graph().nodes)
    assert "supervisor" in nodes
    for worker in WORKERS:
        assert worker in nodes, f"missing specialist worker node '{worker}'"


def test_ac02_supervisor_can_reach_every_worker(tmp_tracer, tmp_path):
    """AC-02: a dispatch edge exists from the supervisor to each specialist."""
    graph = build_graph(tmp_tracer, checkpointer=make_checkpointer(tmp_path / "cp.sqlite"))
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    for worker in WORKERS:
        assert ("supervisor", worker) in edges, f"supervisor cannot dispatch to '{worker}'"
    assert ("supervisor", "finalize") in edges


def test_ac02_every_worker_returns_through_the_critic(tmp_tracer, tmp_path):
    """AC-02/AC-12: no specialist writes to the packet without passing the critic.

    `medication` and `followup` reach the critic via their own conditional routers, so their
    path is indirect but present.
    """
    graph = build_graph(tmp_tracer, checkpointer=make_checkpointer(tmp_path / "cp.sqlite"))
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("summary", "reflect") in edges
    assert ("education", "reflect") in edges
    assert ("medication", "reflect") in edges          # direct branch
    assert ("pharmacist_review", "reflect") in edges   # escalation branch
    assert ("followup", "reflect") in edges            # direct branch
    assert ("enhanced_followup", "reflect") in edges   # high-risk branch


# ---------------------------------------------------------------------------
# Dependency ordering — enforced in code, not merely requested in the prompt
# ---------------------------------------------------------------------------


def test_ac02_followup_is_blocked_until_medication_completes(case_low_risk, state_factory):
    """AC-02: the follow-up plan depends on what reconciliation changed."""
    state = state_factory(case_low_risk, completed=["summary"])
    legal = _legal_next(state)
    assert "followup" not in legal
    assert "medication" in legal


def test_ac02_education_is_blocked_until_medication_and_followup_complete(
    case_low_risk, state_factory
):
    """AC-02: the patient packet must describe final decisions, not provisional ones."""
    state = state_factory(case_low_risk, completed=["summary", "medication"])
    assert "education" not in _legal_next(state)

    state = state_factory(case_low_risk, completed=["summary", "medication", "followup"])
    assert "education" in _legal_next(state)


def test_ac02_completed_workers_are_not_re_offered(case_low_risk, state_factory):
    """AC-02: an accepted workstream is retired from the legal set."""
    state = state_factory(case_low_risk, completed=["summary"])
    assert "summary" not in _legal_next(state)


# ---------------------------------------------------------------------------
# Deterministic fallback (NFR-07)
# ---------------------------------------------------------------------------


def test_ac02_fallback_plan_respects_dependency_order(case_low_risk, state_factory):
    """AC-02/NFR-07: with no model available the supervisor still dispatches in a safe order."""
    state = state_factory(case_low_risk)
    plan = _fallback_plan(state)
    assert plan.next_agent == "summary"
    assert plan.plan.index("medication") < plan.plan.index("followup")
    assert plan.plan.index("followup") < plan.plan.index("education")


def test_ac02_fallback_finalizes_when_all_work_is_done(case_low_risk, state_factory):
    """AC-02: the supervisor terminates rather than looping once every workstream is complete."""
    state = state_factory(case_low_risk, completed=list(WORKERS))
    plan = _fallback_plan(state)
    assert plan.next_agent == "finalize"
    assert plan.plan == []


@pytest.mark.live
def test_ac02_live_run_completes_all_four_workstreams(case_low_risk):
    """AC-02: a live end-to-end run dispatches to and completes all four specialists."""
    from discharge_copilot.context.quarantine import quarantine_all
    from discharge_copilot.state import new_state
    from discharge_copilot.tracing import Tracer

    tracer = Tracer("test_ac02_live", case_id=case_low_risk.case_id)
    graph = build_graph(tracer, with_interrupts=False)
    state = new_state(
        case_id="TEST-AC02",
        session_id="test",
        trace_id="test_ac02_live",
        patient=case_low_risk.patient,
        quarantined_notes=quarantine_all(
            case_low_risk.clinical_notes, case_low_risk.nurse_handoff_notes
        ),
    )
    final = graph.invoke(state, config={"configurable": {"thread_id": "TEST-AC02"}})
    assert set(final["completed"]) == set(WORKERS)
    assert final["status"] == "complete"
