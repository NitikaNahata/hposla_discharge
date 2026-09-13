"""Command-line interface — the single documented entry point (NFR-02).

    python -m discharge_copilot run    --case data/samples/case_001.json
    python -m discharge_copilot run    --case data/samples/case_003.json --pause-after medication
    python -m discharge_copilot resume --case-id CASE-003
    python -m discharge_copilot show   --case-id CASE-003

`run --pause-after` and `resume` are deliberately separate processes: that is what makes the
AC-05 pause/resume evidence meaningful.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from langchain_core.runnables import RunnableConfig
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import get_config
from .context.quarantine import quarantine_all
from .graph import build_graph, clear_thread, make_checkpointer
from .memory import TieredMemory
from .observability import setup_tracing
from .schemas import DischargeCase
from .state import new_state
from .tools import build_toolbox
from .tracing import Tracer, new_trace_id

app = typer.Typer(
    add_completion=False,
    help="Discharge Planning & Follow-up Copilot — a multi-agent coordination aid.",
)
console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_case(path: Path) -> DischargeCase:
    """Load and validate a committed synthetic discharge case."""
    if not path.exists():
        raise typer.BadParameter(f"Case file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    return DischargeCase.model_validate(raw)


def _thread_config(case_id: str) -> RunnableConfig:
    """LangGraph thread configuration. `thread_id` is what makes resume possible (AC-05)."""
    return {"configurable": {"thread_id": case_id}, "recursion_limit": 60}


def _print_packet(packet: dict[str, Any]) -> None:
    """Render the final discharge packet."""
    if not packet:
        console.print("[yellow]No packet was produced.[/yellow]")
        return

    console.print()
    console.rule("[bold]Discharge packet[/bold]")

    summary = packet.get("summary")
    if summary:
        console.print(
            Panel(
                f"[bold]{summary['primary_diagnosis']}[/bold]\n\n"
                f"{summary['hospital_course']}\n\n"
                f"[dim]Condition at discharge:[/dim] {summary['condition_at_discharge']}\n"
                f"[dim]Disposition:[/dim] {summary['disposition']}",
                title="Summary",
                border_style="blue",
            )
        )

    meds = packet.get("medications")
    if meds:
        table = Table(title="Medication reconciliation", show_lines=False)
        table.add_column("Medication")
        table.add_column("Action")
        table.add_column("Reason", overflow="fold")
        for change in meds.get("changes", []):
            action = change["action"]
            colour = {
                "stop": "red", "start": "green", "modify": "yellow", "continue": "dim",
            }.get(action, "white")
            table.add_row(change["medication"], f"[{colour}]{action}[/{colour}]", change["reason"])
        console.print(table)

        for interaction in meds.get("interactions", []):
            sev = interaction["severity"]
            colour = {
                "contraindicated": "bold red", "major": "red",
                "moderate": "yellow", "minor": "dim",
            }.get(sev, "white")
            console.print(
                f"  [{colour}]⚠ {sev.upper()}[/{colour}] "
                f"{interaction['drug_a']} + {interaction['drug_b']}: "
                f"{interaction['description']}"
            )
        if meds.get("unreconciled"):
            console.print(
                f"  [yellow]Unreconciled:[/yellow] {', '.join(meds['unreconciled'])}"
            )

    followup = packet.get("followup")
    if followup:
        table = Table(title="Follow-up plan")
        table.add_column("Specialty")
        table.add_column("Within", justify="right")
        table.add_column("Reason", overflow="fold")
        for appt in followup.get("appointments", []):
            table.add_row(appt["specialty"], f"{appt['within_days']}d", appt["reason"])
        console.print(table)
        if followup.get("enhanced_pathway"):
            console.print("  [magenta]Enhanced follow-up pathway applied[/magenta]")
        for service in followup.get("home_services", []):
            console.print(f"  [dim]home service:[/dim] {service}")

    education = packet.get("education")
    if education:
        body = education["plain_language_summary"]
        if education.get("red_flag_symptoms"):
            body += "\n\n[bold red]Seek help immediately if:[/bold red]\n" + "\n".join(
                f"  • {s}" for s in education["red_flag_symptoms"]
            )
        console.print(Panel(body, title="Patient education", border_style="green"))

    if packet.get("quarantine_flags"):
        console.print(
            Panel(
                "\n".join(f"• {f}" for f in packet["quarantine_flags"])
                + "\n\n[dim]Content was isolated and treated as data, never as "
                "instructions (NFR-03).[/dim]",
                title="[bold yellow]Quarantine flags[/bold yellow]",
                border_style="yellow",
            )
        )

    if packet.get("escalations"):
        console.print(
            Panel(
                "\n".join(f"• {e}" for e in packet["escalations"]),
                title="[bold red]Escalations[/bold red]",
                border_style="red",
            )
        )

    console.print(
        "\n[dim]This packet is a draft coordination aid and requires clinician "
        "approval before use.[/dim]"
    )


def _save_packet_json(case_id: str, packet: dict[str, Any]) -> None:
    """Persist the finalized packet as plain JSON for downstream evaluation (DeepEval)."""
    if not packet:
        return
    cfg = get_config()
    packets_dir = cfg.evidence_dir / "packets"
    packets_dir.mkdir(parents=True, exist_ok=True)
    path = packets_dir / f"{case_id.lower().replace('-', '_')}.json"
    path.write_text(json.dumps(packet, indent=2, default=str))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def run(
    case: Path = typer.Option(..., "--case", "-c", help="Path to a discharge case JSON file."),
    pause_after: str = typer.Option(
        "", "--pause-after", help="Halt before the pharmacist review node (AC-05 demo)."
    ),
    fault_inject: str = typer.Option(
        "", "--fault-inject", help="Force a failure: 'mcp_timeout' | 'llm_error' (AC-12 demo)."
    ),
    fresh: bool = typer.Option(
        False, "--fresh",
        help="Discard any existing checkpoint for this case and start over. "
             "Without it, a completed case RESUMES and finalizes immediately.",
    ),
    trace_suffix: str = typer.Option("", "--trace-suffix", help="Suffix for the trace filename."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress live event echo."),
) -> None:
    """Run a pending discharge through the multi-agent graph."""
    cfg = get_config()
    cfg.ensure_dirs()
    discharge_case = load_case(case)

    if not fresh:
        early_checkpointer = make_checkpointer()
        existing = early_checkpointer.get_tuple(_thread_config(discharge_case.case_id))
        if existing is not None:
            values = existing.checkpoint.get("channel_values", {})
            if values.get("status") in ("complete", "partial") and not existing.pending_writes:
                # Already finished in a prior run: invoking the graph again would just
                # short-circuit to the same result without executing a single node. This
                # check has to happen before ANY Tracer is constructed and before the MCP
                # toolbox is built — build_toolbox() emits a trace event as a side effect
                # of connecting, which would truncate the trace file from whichever run
                # actually did the work. Reading the checkpoint directly avoids all of
                # that: no tracer, no toolbox, no graph.
                console.print(
                    f"[dim]{discharge_case.case_id} already completed in a previous run "
                    f"— reusing checkpoint. Use --fresh to re-run from scratch.[/dim]"
                )
                _save_packet_json(discharge_case.case_id, values.get("packet", {}))
                _print_packet(values.get("packet", {}))
                return

    trace_id = new_trace_id(discharge_case.case_id, trace_suffix)
    tracer = Tracer(trace_id, case_id=discharge_case.case_id, echo=not quiet)
    tracer.register_pii(discharge_case.patient.name, discharge_case.patient.mrn)

    # Optional OpenTelemetry export. Never fails a run — degrades to disabled.
    setup_tracing(tracer)

    console.print(
        Panel(
            f"[bold]{discharge_case.case_id}[/bold] · "
            f"{discharge_case.patient.primary_diagnosis}\n"
            f"[dim]session {discharge_case.session_id} · trace {trace_id}[/dim]"
            + (f"\n[yellow]fault injection: {fault_inject}[/yellow]" if fault_inject else "")
            + ("\n[yellow]will pause before pharmacist review[/yellow]" if pause_after else ""),
            title="Discharge Copilot",
            border_style="cyan",
        )
    )

    notes = quarantine_all(
        discharge_case.clinical_notes, discharge_case.nurse_handoff_notes
    )
    state = new_state(
        case_id=discharge_case.case_id,
        session_id=discharge_case.session_id,
        trace_id=trace_id,
        patient=discharge_case.patient,
        quarantined_notes=notes,
        fault_inject=fault_inject,
    )

    tools, _mcp_client = build_toolbox(
        tracer, fault="timeout" if fault_inject == "mcp_timeout" else ""
    )
    memory = TieredMemory(tracer=tracer)
    checkpointer = make_checkpointer()
    graph = build_graph(
        tracer,
        memory=memory,
        checkpointer=checkpointer,
        with_interrupts=bool(pause_after),
        tools=tools,
    )
    config = _thread_config(discharge_case.case_id)

    if fresh:
        cleared = clear_thread(checkpointer, discharge_case.case_id)
        tracer.emit("checkpoint_cleared", thread_id=discharge_case.case_id, existed=cleared)
        if cleared:
            console.print(
                f"[dim]Discarded the existing checkpoint for "
                f"{discharge_case.case_id}; starting fresh.[/dim]"
            )

    tracer.emit(
        "run_start",
        session_id=discharge_case.session_id,
        pause_after=pause_after,
        fault_inject=fault_inject,
        notes_quarantined=len(notes),
        tools_loaded=[t.name for t in tools],
    )

    final = graph.invoke(state, config=config)

    snapshot = graph.get_state(config)
    if snapshot.next:
        # Interrupted (AC-05). State is checkpointed; a separate process can resume.
        tracer.emit(
            "run_paused", next_nodes=list(snapshot.next), thread_id=discharge_case.case_id
        )
        console.print(
            Panel(
                f"Paused before: [bold]{', '.join(snapshot.next)}[/bold]\n"
                f"State checkpointed to [dim]{cfg.checkpoint_db}[/dim] under thread "
                f"[bold]{discharge_case.case_id}[/bold].\n\n"
                f"Resume in a NEW process with:\n"
                f"  [cyan]python -m discharge_copilot resume "
                f"--case-id {discharge_case.case_id}[/cyan]",
                title="[yellow]Paused for human review[/yellow]",
                border_style="yellow",
            )
        )
        raise typer.Exit(0)

    tracer.emit(
        "run_complete",
        status=final.get("status"),
        counts=tracer.counts(),
        usage=tracer.usage_summary(),
    )
    _save_packet_json(discharge_case.case_id, final.get("packet", {}))
    _print_packet(final.get("packet", {}))
    console.print(f"\n[dim]Trace: {tracer.path}[/dim]")


@app.command()
def resume(
    case_id: str = typer.Option(..., "--case-id", help="Thread id of the paused case."),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Resume a paused discharge from its checkpoint (AC-05).

    Intended to run as a *separate process* from the one that paused it — that is the point of
    the demonstration.
    """
    cfg = get_config()
    cfg.ensure_dirs()

    tracer = Tracer(new_trace_id(case_id, "resume"), case_id=case_id, echo=not quiet)
    tools, _ = build_toolbox(tracer)
    memory = TieredMemory(tracer=tracer)
    checkpointer = make_checkpointer()
    graph = build_graph(
        tracer, memory=memory, checkpointer=checkpointer, with_interrupts=False, tools=tools
    )
    config = _thread_config(case_id)

    snapshot = graph.get_state(config)
    if not snapshot.created_at:
        console.print(f"[red]No checkpoint found for thread '{case_id}'.[/red]")
        raise typer.Exit(1)

    restored = snapshot.values
    console.print(
        Panel(
            f"Restored checkpoint for [bold]{case_id}[/bold]\n"
            f"[dim]checkpointed at {snapshot.created_at}[/dim]\n"
            f"Resuming at: [bold]{', '.join(snapshot.next) or 'graph start'}[/bold]\n\n"
            f"State recovered from the previous process:\n"
            f"  completed workstreams : {restored.get('completed', [])}\n"
            f"  supervisor steps      : {restored.get('supervisor_steps', 0)}\n"
            f"  escalations           : {len(restored.get('escalations', []))}\n"
            f"  risk tier             : "
            f"{restored['risk'].tier.value if restored.get('risk') else 'n/a'}",
            title="[green]Resumed from checkpoint[/green]",
            border_style="green",
        )
    )
    tracer.emit(
        "resume_start",
        thread_id=case_id,
        checkpointed_at=str(snapshot.created_at),
        resuming_at=list(snapshot.next),
        recovered_completed=restored.get("completed", []),
        recovered_steps=restored.get("supervisor_steps", 0),
        note="New OS process — state came from the SQLite checkpoint, not from memory.",
    )

    # `None` as input tells LangGraph to continue from the checkpoint rather than start over.
    final = graph.invoke(None, config=config)

    tracer.emit(
        "run_complete",
        status=final.get("status"),
        counts=tracer.counts(),
        usage=tracer.usage_summary(),
    )
    _save_packet_json(case_id, final.get("packet", {}))
    _print_packet(final.get("packet", {}))
    console.print(f"\n[dim]Trace: {tracer.path}[/dim]")


@app.command()
def show(
    case_id: str = typer.Option(..., "--case-id", help="Thread id to inspect."),
) -> None:
    """Show the checkpointed state for a case without running anything."""
    tracer = Tracer(new_trace_id(case_id, "inspect"), case_id=case_id)
    graph = build_graph(tracer, checkpointer=make_checkpointer(), with_interrupts=False)
    snapshot = graph.get_state(_thread_config(case_id))
    if not snapshot.created_at:
        console.print(f"[red]No checkpoint found for thread '{case_id}'.[/red]")
        raise typer.Exit(1)
    values = snapshot.values
    console.print(
        Panel(
            f"checkpointed at : {snapshot.created_at}\n"
            f"next nodes      : {list(snapshot.next) or ['<complete>']}\n"
            f"status          : {values.get('status')}\n"
            f"completed       : {values.get('completed', [])}\n"
            f"supervisor steps: {values.get('supervisor_steps', 0)}\n"
            f"reflections     : {len(values.get('reflections', []))}\n"
            f"escalations     : {len(values.get('escalations', []))}",
            title=f"Checkpoint · {case_id}",
            border_style="cyan",
        )
    )
    _print_packet(values.get("packet", {}))


@app.command()
def memory(
    patient: str = typer.Option(..., "--patient", "-p", help="Patient MRN, e.g. MRN-2001."),
    evict: bool = typer.Option(False, "--evict", help="Apply the eviction policy after showing."),
) -> None:
    """Inspect what the copilot remembers about a patient across sessions (AC-06..AC-08)."""
    mem = TieredMemory()
    snapshot = mem.snapshot(patient)

    if not snapshot["facts"]:
        console.print(f"[yellow]No memory stored for {patient}.[/yellow]")
        raise typer.Exit(0)

    console.print(
        Panel(
            f"episodic facts : {snapshot['episodic_count']}\n"
            f"semantic facts : {snapshot['semantic_count']}\n"
            f"sessions seen  : {', '.join(snapshot['sessions'])}",
            title=f"Tiered memory · {patient}",
            border_style="cyan",
        )
    )

    table = Table(title="Stored facts", show_lines=False)
    table.add_column("Key", overflow="fold")
    table.add_column("Kind")
    table.add_column("Base", justify="right")
    table.add_column("Effective", justify="right")
    table.add_column("Hits", justify="right")
    table.add_column("Session")
    table.add_column("Fact", overflow="fold")
    for fact in snapshot["facts"]:
        style = "bold" if fact["permanent"] else ""
        table.add_row(
            fact["key"],
            fact["kind"],
            f"{fact['base_importance']:.2f}",
            f"{fact['effective_importance']:.3f}",
            str(fact["access_count"]),
            fact["session_id"],
            fact["content"],
            style=style,
        )
    console.print(table)
    console.print("[dim]Bold rows are permanent — exempt from TTL expiry (AC-08).[/dim]")

    if evict:
        result = mem.episodic.evict(patient)
        console.print(
            Panel(
                f"evaluated {result['evaluated']} · kept {result['kept']} · "
                f"evicted {result['evicted']} "
                f"(TTL {result['expired_by_ttl']}, capacity {result['evicted_by_capacity']})",
                title="Eviction applied",
                border_style="yellow",
            )
        )
        for key, reason in result["reasons"].items():
            console.print(f"  [dim]{key}:[/dim] {reason}")


def main() -> None:
    try:
        app()
    except RuntimeError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
