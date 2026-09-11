"""AC-01 — The system is built on LangGraph with an explicit typed state object shared across nodes.

Verifies that `DischargeState` is a real `TypedDict`, that worker output fields are typed as
Pydantic models, and that accumulator fields carry reducers so concurrent node returns merge
rather than clobber.
"""

from __future__ import annotations

import typing

import pytest
from pydantic import BaseModel

from discharge_copilot import state as state_mod
from discharge_copilot.schemas import (
    DischargeSummary,
    EducationPacket,
    FollowUpPlan,
    MedicationReconciliation,
    PatientRecord,
    RiskAssessment,
)
from discharge_copilot.state import WORKERS, DischargeState, merge_dicts, remaining_workers


def test_ac01_state_is_a_typeddict():
    """AC-01: the shared state object is an explicit TypedDict."""
    assert typing.is_typeddict(DischargeState)
    # `total=False` — nodes return partial updates, which LangGraph merges.
    assert DischargeState.__total__ is False


def test_ac01_state_declares_all_required_fields():
    """AC-01: state carries identity, inputs, control, outputs and evidence fields."""
    hints = typing.get_type_hints(DischargeState, include_extras=True)
    for field in (
        "case_id", "session_id", "trace_id",          # identity
        "patient", "quarantined_notes",               # inputs
        "messages", "compressed_history",             # working memory
        "plan", "next_agent", "completed",            # supervisor control
        "risk", "summary", "medications",             # worker outputs
        "followup", "education",
        "reflections", "retry_counts", "tool_failures",  # self-healing
        "memory_hits", "memory_writes",               # memory
        "escalations", "routing_decisions",           # evidence
        "packet", "status",
    ):
        assert field in hints, f"DischargeState is missing '{field}'"


def test_ac01_worker_outputs_are_pydantic_models():
    """AC-01/AC-04: every worker output field is typed as a validated Pydantic model."""
    hints = typing.get_type_hints(DischargeState, include_extras=True)
    expected = {
        "patient": PatientRecord,
        "risk": RiskAssessment,
        "summary": DischargeSummary,
        "medications": MedicationReconciliation,
        "followup": FollowUpPlan,
        "education": EducationPacket,
    }
    for field, model in expected.items():
        assert hints[field] is model, f"{field} should be typed as {model.__name__}"
        assert issubclass(model, BaseModel)


@pytest.mark.parametrize(
    "field",
    ["completed", "reflections", "escalations", "tool_failures",
     "routing_decisions", "memory_writes", "compression_events"],
)
def test_ac01_accumulator_fields_have_reducers(field):
    """AC-01: accumulators are Annotated with a reducer so returns append, not overwrite.

    Without a reducer, a self-healing retry that emits a second reflection would erase the
    first — destroying the very evidence AC-12 depends on.
    """
    hints = typing.get_type_hints(DischargeState, include_extras=True)
    annotation = hints[field]
    assert typing.get_origin(annotation) is typing.Annotated, (
        f"'{field}' accumulates across nodes and must declare a reducer"
    )
    assert len(typing.get_args(annotation)) >= 2


def test_ac01_retry_counts_uses_a_merge_reducer():
    """AC-01: per-worker retry counters merge rather than replace."""
    hints = typing.get_type_hints(DischargeState, include_extras=True)
    reducer = typing.get_args(hints["retry_counts"])[1]
    assert reducer is merge_dicts
    assert merge_dicts({"summary": 1}, {"medication": 2}) == {"summary": 1, "medication": 2}
    assert merge_dicts({"summary": 1}, {"summary": 3}) == {"summary": 3}


def test_ac01_messages_field_uses_langgraph_reducer():
    """AC-01: the message channel uses LangGraph's add_messages reducer."""
    from langgraph.graph.message import add_messages

    hints = typing.get_type_hints(DischargeState, include_extras=True)
    assert typing.get_args(hints["messages"])[1] is add_messages


def test_ac01_new_state_initialises_every_channel(case_low_risk, state_factory):
    """AC-01: `new_state` produces a fully-populated, typed starting state."""
    state = state_factory(case_low_risk)
    assert state["case_id"] == "CASE-001"
    assert isinstance(state["patient"], PatientRecord)
    assert state["completed"] == []
    assert state["plan"] == list(WORKERS)
    assert state["status"] == "pending"
    assert state["retry_counts"] == {}


def test_ac01_remaining_workers_tracks_completion(case_low_risk, state_factory):
    """AC-01: derived helpers read the typed state rather than re-deriving from messages."""
    state = state_factory(case_low_risk)
    assert remaining_workers(state) == list(WORKERS)
    state["completed"] = ["summary", "medication"]
    assert remaining_workers(state) == ["followup", "education"]


def test_ac01_worker_output_helper_maps_names_to_state_keys(case_low_risk, state_factory):
    """AC-01: the 'medication' worker writes to the 'medications' state key."""
    state = state_factory(case_low_risk)
    state["medications"] = MedicationReconciliation()
    assert state_mod.worker_output(state, "medication") is state["medications"]
    assert state_mod.worker_output(state, "summary") is None
