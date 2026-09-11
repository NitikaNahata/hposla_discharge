"""AC-03 — The graph uses conditional edges to route on state.

Every router is a pure function of state, so each branch is exercised here by constructing a
state dict directly — no LLM, no network. Routing logic that can only be reached through a full
live run is routing logic whose edges are never actually tested.
"""

from __future__ import annotations

import pytest

from discharge_copilot.graph import (
    route_after_medication,
    route_after_reflection,
    route_from_supervisor,
    route_risk_tier,
)
from discharge_copilot.nodes.intake import assess_risk
from discharge_copilot.schemas import (
    FollowUpPlan,
    MedicationInteraction,
    MedicationReconciliation,
    RiskAssessment,
    RiskTier,
    Severity,
)

# ---------------------------------------------------------------------------
# Router 1 — supervisor dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "choice", ["summary", "medication", "followup", "education"]
)
def test_ac03_supervisor_dispatches_to_each_worker(choice):
    """AC-03: the supervisor's choice routes to the matching worker node."""
    assert route_from_supervisor({"next_agent": choice}) == choice


def test_ac03_supervisor_routes_to_finalize():
    """AC-03: 'finalize' terminates the worker loop."""
    assert route_from_supervisor({"next_agent": "finalize"}) == "finalize"


@pytest.mark.parametrize("bad", ["", "pharmacy", None, "SUMMARY "])
def test_ac03_supervisor_falls_through_to_finalize_on_bad_choice(bad):
    """AC-03/NFR-07: an unroutable choice yields a partial packet, never an exception."""
    assert route_from_supervisor({"next_agent": bad}) == "finalize"


# ---------------------------------------------------------------------------
# Router 2 — pharmacist review on interaction severity
# ---------------------------------------------------------------------------


def _recon(severity: Severity | None, claimed: bool = False) -> MedicationReconciliation:
    interactions = (
        [
            MedicationInteraction(
                drug_a="Warfarin", drug_b="Fluconazole",
                severity=severity, description="synthetic test interaction",
            )
        ]
        if severity
        else []
    )
    return MedicationReconciliation(
        interactions=interactions, pharmacist_review_required=claimed
    )


@pytest.mark.parametrize("severity", [Severity.MAJOR, Severity.CONTRAINDICATED])
def test_ac03_serious_interaction_routes_to_pharmacist_review(severity):
    """AC-03: major and contraindicated interactions interrupt for pharmacist sign-off."""
    assert route_after_medication({"medications": _recon(severity)}) == "pharmacist_review"


@pytest.mark.parametrize("severity", [Severity.MINOR, Severity.MODERATE, None])
def test_ac03_lesser_interactions_proceed_to_reflection(severity):
    """AC-03: minor and moderate interactions do not escalate — escalation precision matters."""
    assert route_after_medication({"medications": _recon(severity)}) == "reflect"


def test_ac03_pharmacist_routing_is_derived_not_model_asserted():
    """AC-03: severity drives the branch even when the model's own boolean disagrees.

    The model sets `pharmacist_review_required` inconsistently. Routing reads the interaction
    list structurally via `needs_pharmacist()`, so a forgotten flag cannot suppress an
    escalation.
    """
    recon = _recon(Severity.CONTRAINDICATED, claimed=False)
    assert recon.pharmacist_review_required is False
    assert recon.needs_pharmacist() is True
    assert route_after_medication({"medications": recon}) == "pharmacist_review"


def test_ac03_model_claimed_review_is_honoured_without_interactions():
    """AC-03: an explicit model request for review is respected even with no interaction listed."""
    assert route_after_medication({"medications": _recon(None, claimed=True)}) == "pharmacist_review"


def test_ac03_missing_reconciliation_does_not_crash_routing():
    """AC-03/NFR-07: an absent artifact degrades to the critic rather than raising."""
    assert route_after_medication({}) == "reflect"


# ---------------------------------------------------------------------------
# Router 3 — enhanced follow-up on readmission risk
# ---------------------------------------------------------------------------


def test_ac03_high_risk_routes_to_enhanced_followup():
    """AC-03: a high readmission-risk tier diverts to the enhanced follow-up path."""
    decision = route_risk_tier(
        {"risk": RiskAssessment(tier=RiskTier.HIGH, score=0.72), "followup": FollowUpPlan()}
    )
    assert decision == "enhanced_followup"


@pytest.mark.parametrize("tier,score", [(RiskTier.LOW, 0.10), (RiskTier.MODERATE, 0.42)])
def test_ac03_lower_risk_skips_enhanced_pathway(tier, score):
    """AC-03: low and moderate risk take the standard path."""
    decision = route_risk_tier(
        {"risk": RiskAssessment(tier=tier, score=score), "followup": FollowUpPlan()}
    )
    assert decision == "reflect"


def test_ac03_no_risk_assessment_skips_enhanced_pathway():
    """AC-03/NFR-07: a missing risk assessment does not divert the run."""
    assert route_risk_tier({"followup": FollowUpPlan()}) == "reflect"


def test_ac03_risk_routing_is_driven_by_the_committed_sample_cases(
    case_low_risk, case_high_risk
):
    """AC-03: the committed samples genuinely exercise both branches of this router.

    A conditional edge whose alternate branch no committed input can reach is untested in
    practice, whatever the unit tests say.
    """
    low = assess_risk(case_low_risk.patient)
    high = assess_risk(case_high_risk.patient)
    assert low.tier == RiskTier.LOW
    assert high.tier == RiskTier.HIGH
    assert route_risk_tier({"risk": low, "followup": FollowUpPlan()}) == "reflect"
    assert (
        route_risk_tier({"risk": high, "followup": FollowUpPlan()}) == "enhanced_followup"
    )


# ---------------------------------------------------------------------------
# Router 4 — reflection: accept / self-heal / escalate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "worker", ["summary", "medication", "followup", "education"]
)
def test_ac03_revision_routes_back_to_the_same_worker(worker):
    """AC-03/AC-12: a revision request loops back to the worker that produced the artifact."""
    state = {"pending_revision": {"worker": worker, "issues": ["missing red flags"]}}
    assert route_after_reflection(state) == worker


@pytest.mark.parametrize("pending", [{}, None, {"worker": ""}, {"worker": "pharmacy"}])
def test_ac03_accepted_output_returns_to_supervisor(pending):
    """AC-03: accept and escalate both return control to the supervisor."""
    assert route_after_reflection({"pending_revision": pending}) == "supervisor"


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


def test_ac03_graph_declares_four_conditional_routers(tmp_tracer, tmp_path):
    """AC-03: the compiled graph wires all four conditional edge functions."""
    from discharge_copilot.graph import build_graph, make_checkpointer

    graph = build_graph(
        tmp_tracer, checkpointer=make_checkpointer(tmp_path / "cp.sqlite")
    )
    nodes = set(graph.get_graph().nodes)
    for node in (
        "intake", "supervisor", "summary", "medication", "followup", "education",
        "pharmacist_review", "enhanced_followup", "reflect", "finalize",
    ):
        assert node in nodes, f"graph is missing node '{node}'"

    # Branch targets that only a conditional edge can reach.
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("medication", "pharmacist_review") in edges
    assert ("medication", "reflect") in edges
    assert ("followup", "enhanced_followup") in edges
    assert ("followup", "reflect") in edges
    assert ("reflect", "supervisor") in edges
    assert ("reflect", "medication") in edges
