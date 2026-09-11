"""The typed graph state (AC-01).

`DischargeState` is the single object every node reads from and writes to. It is a `TypedDict`
because LangGraph merges *partial* dicts returned by nodes — a Pydantic model at the top level
would force every node to reconstruct the whole object. The values inside it are Pydantic models,
so the payloads are still strictly validated (AC-04).

Three kinds of field appear here, and the distinction matters:

* **Scalar / replace fields** — plain annotations. A node returning the key overwrites it.
* **Accumulator fields** — `Annotated[list[...], operator.add]`. Node returns are appended, never
  clobbered, so a self-healing retry adds a reflection rather than erasing the previous one.
* **Merge fields** — `Annotated[dict, merge_dicts]`. Used for per-worker counters that several
  nodes update independently.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from .schemas import (
    DischargeSummary,
    EducationPacket,
    FollowUpPlan,
    MedicationReconciliation,
    PatientRecord,
    Reflection,
    RiskAssessment,
)


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer: shallow-merge two dicts, right winning on conflict."""
    return {**(left or {}), **(right or {})}


def keep_last(left: Any, right: Any) -> Any:
    """Reducer: last non-None write wins. Lets a node explicitly decline to update a field."""
    return left if right is None else right


class QuarantinedNote(TypedDict):
    """An untrusted clinical free-text note, isolated per NFR-03.

    Never rendered into a prompt directly — `context/quarantine.py` fences it and marks it
    as data. `injection_flags` records any imperative patterns detected during scanning.
    """

    note_id: str
    source: str
    content: str
    injection_flags: list[str]
    quarantined_at: str


class MemoryHit(TypedDict):
    """A memory item recalled into the current run, with provenance."""

    tier: str
    key: str
    content: str
    importance: float
    score: float
    session_id: str


class ToolFailure(TypedDict):
    """A tool invocation that failed — input to the self-healing loop (AC-12, NFR-07)."""

    tool: str
    error: str
    attempt: int
    recovered: bool
    fallback_used: str


class DischargeState(TypedDict, total=False):
    """Typed state shared across every node in the graph (AC-01)."""

    # --- Identity -----------------------------------------------------------
    case_id: str
    session_id: str
    trace_id: str

    # --- Inputs -------------------------------------------------------------
    patient: PatientRecord
    # Untrusted free-text, isolated at intake and never treated as instructions (NFR-03).
    quarantined_notes: list[QuarantinedNote]

    # --- Conversation / working memory (T1) ---------------------------------
    messages: Annotated[list[AnyMessage], add_messages]
    # Rolling summary produced by the compression middleware (NFR-08).
    compressed_history: str
    compression_events: Annotated[list[dict[str, Any]], operator.add]

    # --- Supervisor control -------------------------------------------------
    plan: list[str]
    next_agent: str
    completed: Annotated[list[str], operator.add]
    supervisor_steps: int

    # --- Worker outputs (AC-04: each a validated Pydantic model) ------------
    risk: RiskAssessment
    summary: DischargeSummary
    medications: MedicationReconciliation
    followup: FollowUpPlan
    education: EducationPacket

    # --- Reflection / self-healing (AC-12) ----------------------------------
    reflections: Annotated[list[Reflection], operator.add]
    # Per-worker retry counters, merged rather than replaced so concurrent
    # worker returns do not clobber one another's counts.
    retry_counts: Annotated[dict[str, int], merge_dicts]
    tool_failures: Annotated[list[ToolFailure], operator.add]
    # Set when the critic asks a worker to revise; consumed on the retry.
    pending_revision: dict[str, Any]

    # --- Memory (AC-06) -----------------------------------------------------
    memory_hits: list[MemoryHit]
    memory_writes: Annotated[list[dict[str, Any]], operator.add]

    # --- Escalations / routing evidence -------------------------------------
    escalations: Annotated[list[str], operator.add]
    # Every conditional-edge decision, recorded for the AC-03 evidence trail.
    routing_decisions: Annotated[list[dict[str, Any]], operator.add]

    # --- Terminal -----------------------------------------------------------
    packet: dict[str, Any]
    status: str
    fault_inject: str


# The four workstreams the supervisor dispatches to (AC-02).
WORKERS: tuple[str, ...] = ("summary", "medication", "followup", "education")


def new_state(
    *,
    case_id: str,
    session_id: str,
    trace_id: str,
    patient: PatientRecord,
    quarantined_notes: list[QuarantinedNote],
    fault_inject: str = "",
) -> DischargeState:
    """Build the initial state for a run. Keeps node code free of defaulting logic."""
    return DischargeState(
        case_id=case_id,
        session_id=session_id,
        trace_id=trace_id,
        patient=patient,
        quarantined_notes=quarantined_notes,
        messages=[],
        compressed_history="",
        compression_events=[],
        plan=list(WORKERS),
        next_agent="",
        completed=[],
        supervisor_steps=0,
        reflections=[],
        retry_counts={},
        tool_failures=[],
        pending_revision={},
        memory_hits=[],
        memory_writes=[],
        escalations=[],
        routing_decisions=[],
        packet={},
        status="pending",
        fault_inject=fault_inject,
    )


def remaining_workers(state: DischargeState) -> list[str]:
    """Workstreams not yet accepted into state. Used by the supervisor and by routing."""
    done = set(state.get("completed", []))
    return [w for w in WORKERS if w not in done]


def worker_output(state: DischargeState, worker: str) -> Any:
    """Fetch a worker's output from state by worker name."""
    return state.get({"medication": "medications"}.get(worker, worker))
