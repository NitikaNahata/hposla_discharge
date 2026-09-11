"""Graph topology, conditional routing and checkpointing (AC-02, AC-03, AC-05).

    START ─▶ intake ─▶ supervisor
                          │
             route_from_supervisor ─┬─▶ summary ───────────────────────▶ reflect
                                    ├─▶ medication ─ route_after_medication
                                    │                   ├─▶ pharmacist_review ─▶ reflect
                                    │                   └─▶ reflect
                                    ├─▶ followup ── route_risk_tier
                                    │                   ├─▶ enhanced_followup ─▶ reflect
                                    │                   └─▶ reflect
                                    ├─▶ education ──────────────────────▶ reflect
                                    └─▶ finalize ─▶ END
                                                        │
                                          route_after_reflection
                                             ├─▶ <same worker>  (revise — self-heal, AC-12)
                                             ├─▶ compress ─▶ supervisor  (NFR-08)
                                             └─▶ supervisor    (accept / escalate)

Four conditional edge functions, each a **pure function of state**:

* `route_from_supervisor`  — dispatch to a worker or finalize
* `route_after_medication` — pharmacist review on major/contraindicated interactions
* `route_risk_tier`        — enhanced follow-up path on high readmission risk
* `route_after_reflection` — accept, self-heal, compress, or escalate

Keeping them pure is deliberate: every branch is unit-tested by constructing a state dict, with
no LLM and no network (`tests/test_ac03_conditional_routing.py`). Routing logic that can only be
exercised by a full live run is routing logic that is never actually tested at its edges.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .config import get_config
from .nodes.compress import make_compress_node, should_compress
from .nodes.intake import make_intake_node
from .nodes.reflection import make_reflection_node
from .nodes.supervisor import make_supervisor_node
from .nodes.terminal import (
    make_enhanced_followup_node,
    make_finalize_node,
    make_pharmacist_review_node,
)
from .nodes.workers import (
    make_education_node,
    make_followup_node,
    make_medication_node,
    make_summary_node,
)
from .state import DischargeState

WORKER_NODES = ("summary", "medication", "followup", "education")


# ---------------------------------------------------------------------------
# Conditional routers (AC-03) — pure functions of state
# ---------------------------------------------------------------------------


def route_from_supervisor(state: DischargeState) -> str:
    """Dispatch the supervisor's choice to a worker node, or finalize.

    Guards against a malformed choice by falling through to finalize rather than raising —
    an unroutable state should still yield a partial packet (NFR-07).
    """
    choice = (state.get("next_agent") or "").strip()
    if choice in WORKER_NODES:
        return choice
    return "finalize"


def route_after_medication(state: DischargeState) -> str:
    """Route to pharmacist review when reconciliation surfaced a serious interaction.

    The decision reads `needs_pharmacist()`, which is derived structurally from interaction
    severities — not from the model's own boolean, which it may set inconsistently.
    """
    recon = state.get("medications")
    if recon is not None and recon.needs_pharmacist():
        return "pharmacist_review"
    return "reflect"


def route_risk_tier(state: DischargeState) -> str:
    """Route a high readmission risk to the enhanced follow-up path."""
    risk = state.get("risk")
    plan = state.get("followup")
    if risk is not None and risk.tier.value == "high" and plan is not None:
        return "enhanced_followup"
    return "reflect"


def route_after_reflection(state: DischargeState) -> str:
    """Accept, self-heal, escalate — or compress first (AC-12, NFR-08).

    'revise' sends control back to the *same worker*, which will see the critic's issues in its
    context. Otherwise control returns to the supervisor, via the compression middleware when
    the transcript has outgrown its budget.

    Compression is checked here rather than on its own timer because this is the natural quiet
    point in the cycle: a workstream has just been retired, so the transcript is at a local
    maximum and nothing is mid-flight.
    """
    pending = state.get("pending_revision") or {}
    worker = pending.get("worker")
    if worker in WORKER_NODES:
        return worker
    if should_compress(state):
        return "compress"
    return "supervisor"


# ---------------------------------------------------------------------------
# Checkpointer (AC-05)
# ---------------------------------------------------------------------------


def make_checkpointer(db_path: Any = None) -> SqliteSaver:
    """Open a durable SQLite checkpointer.

    Constructed from a raw connection rather than `SqliteSaver.from_conn_string`, which is a
    context manager that closes the database on exit. AC-05 requires the checkpoint to outlive
    the *process*, so the connection's lifetime must not be scoped to a `with` block.

    `check_same_thread=False` because LangGraph may execute nodes on a worker thread.
    """
    cfg = get_config()
    path = db_path or cfg.checkpoint_db
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn)


def clear_thread(checkpointer: SqliteSaver, thread_id: str) -> bool:
    """Discard a case's checkpoint history so it starts from scratch.

    Durable checkpointing has a sharp edge: `thread_id = case_id`, so re-running a case that
    already completed *resumes* it, and the supervisor immediately finalizes because every
    workstream is already marked complete. That is correct resume behaviour, and exactly wrong
    when the intent was to run the case again.

    `run --fresh` calls this so a re-run is genuinely a re-run.

    Returns True if a checkpoint existed and was removed.
    """
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": ""}
    }
    existed = checkpointer.get_tuple(config) is not None
    if existed:
        checkpointer.delete_thread(thread_id)
    return existed


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_graph(
    tracer: Any,
    *,
    memory: Any = None,
    checkpointer: Any = None,
    with_interrupts: bool = True,
    tools: list[Any] | None = None,
):
    """Build and compile the discharge-planning graph.

    Args:
        tracer: `Tracer` receiving every node, routing and tool event (NFR-04).
        memory: tiered-memory facade, or None to run without persistence.
        checkpointer: a checkpointer, or None to build the default SQLite one (AC-05).
        with_interrupts: halt before `pharmacist_review` for human sign-off.
        tools: MCP + agentic-RAG tools to bind to the workers (AC-10, AC-11). Each worker
            receives only the subset listed in `WORKER_TOOLS` — tool access is part of the
            context-isolation boundary, not a shared pool.
    """
    graph: StateGraph = StateGraph(DischargeState)
    tools = tools or []

    # --- Nodes (AC-02) ---
    graph.add_node("intake", make_intake_node(tracer, memory=memory))
    graph.add_node("supervisor", make_supervisor_node(tracer))
    graph.add_node("summary", make_summary_node(tracer, tools))
    graph.add_node("medication", make_medication_node(tracer, tools))
    graph.add_node("followup", make_followup_node(tracer, tools))
    graph.add_node("education", make_education_node(tracer, tools))
    graph.add_node("pharmacist_review", make_pharmacist_review_node(tracer))
    graph.add_node("enhanced_followup", make_enhanced_followup_node(tracer))
    graph.add_node("reflect", make_reflection_node(tracer))
    graph.add_node("compress", make_compress_node(tracer))
    graph.add_node("finalize", make_finalize_node(tracer, memory=memory))

    # --- Edges ---
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "supervisor")

    # Conditional #1 — supervisor dispatch
    graph.add_conditional_edges(
        "supervisor",
        _traced(tracer, "route_from_supervisor", route_from_supervisor),
        {
            "summary": "summary",
            "medication": "medication",
            "followup": "followup",
            "education": "education",
            "finalize": "finalize",
        },
    )

    # Workers with no branch of their own go straight to the critic.
    graph.add_edge("summary", "reflect")
    graph.add_edge("education", "reflect")

    # Conditional #2 — pharmacist review on serious interactions
    graph.add_conditional_edges(
        "medication",
        _traced(tracer, "route_after_medication", route_after_medication),
        {"pharmacist_review": "pharmacist_review", "reflect": "reflect"},
    )
    graph.add_edge("pharmacist_review", "reflect")

    # Conditional #3 — enhanced follow-up on high readmission risk
    graph.add_conditional_edges(
        "followup",
        _traced(tracer, "route_risk_tier", route_risk_tier),
        {"enhanced_followup": "enhanced_followup", "reflect": "reflect"},
    )
    graph.add_edge("enhanced_followup", "reflect")

    # Conditional #4 — reflection: accept / self-heal / escalate
    graph.add_conditional_edges(
        "reflect",
        _traced(tracer, "route_after_reflection", route_after_reflection),
        {
            "summary": "summary",
            "medication": "medication",
            "followup": "followup",
            "education": "education",
            "compress": "compress",
            "supervisor": "supervisor",
        },
    )
    graph.add_edge("compress", "supervisor")

    graph.add_edge("finalize", END)

    if checkpointer is None:
        checkpointer = make_checkpointer()

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["pharmacist_review"] if with_interrupts else None,
    )


def _traced(tracer: Any, name: str, fn):
    """Wrap a router so every branch decision lands in the trace (AC-03 evidence)."""

    def wrapped(state: DischargeState) -> str:
        decision = fn(state)
        tracer.routing(
            router=name,
            decision=decision,
            reason=_reason(name, state, decision),
            completed=list(state.get("completed", [])),
        )
        return decision

    wrapped.__name__ = f"traced_{name}"
    return wrapped


def _reason(name: str, state: DischargeState, decision: str) -> str:
    """Human-readable justification for a routing decision, recorded in the trace."""
    if name == "route_from_supervisor":
        return f"supervisor selected '{state.get('next_agent')}'"
    if name == "route_after_medication":
        recon = state.get("medications")
        if recon is None:
            return "no reconciliation artifact; proceeding to critic"
        sev = recon.highest_severity
        return (
            f"highest interaction severity={sev.value if sev else 'none'}; "
            f"pharmacist_required={recon.needs_pharmacist()}"
        )
    if name == "route_risk_tier":
        risk = state.get("risk")
        return (
            f"readmission risk tier={risk.tier.value} (score {risk.score:.2f})"
            if risk
            else "no risk assessment available"
        )
    if name == "route_after_reflection":
        pending = state.get("pending_revision") or {}
        if pending.get("worker"):
            return (
                f"critic requested revision of '{pending['worker']}': "
                f"{len(pending.get('issues', []))} issue(s)"
            )
        if decision == "compress":
            from .memory import working

            return (
                "transcript exceeded the compression threshold "
                f"(~{working.token_estimate(state.get('messages', []) or [])} tokens)"
            )
        return "critic accepted or escalated; returning to supervisor"
    return ""
