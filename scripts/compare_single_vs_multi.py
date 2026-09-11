"""Measured single-agent vs multi-agent comparison (NFR-06, Good-to-Have).

    python scripts/compare_single_vs_multi.py
    python scripts/compare_single_vs_multi.py --cases case_002 case_003

Runs both orchestrations over the same cases with the same model, the same tools and the same
schemas. Only the topology differs.

Writes `evidence/logs/comparison.json` and rewrites the results block in
`docs/single-vs-multi-agent.md`.

The comparison reports what it measures, including where the single agent wins. A comparison that
could only confirm the decision already made would not be worth running.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from discharge_copilot.cli import load_case
from discharge_copilot.config import get_config
from discharge_copilot.context.quarantine import quarantine_all
from discharge_copilot.graph import build_graph, make_checkpointer
from discharge_copilot.graph_single import run_single_agent
from discharge_copilot.memory import TieredMemory
from discharge_copilot.nodes.intake import assess_risk
from discharge_copilot.state import new_state
from discharge_copilot.tools import build_toolbox
from discharge_copilot.tracing import Tracer

CFG = get_config()

# Conflicts deliberately seeded into the sample cases. Detection recall is measured
# against this ground truth, not against whatever either variant happened to report.
SEEDED_CONFLICTS: dict[str, list[tuple[str, str, str]]] = {
    "case_001": [],
    "case_002": [
        ("furosemide", "ibuprofen", "major"),
        ("apixaban", "ibuprofen", "major"),
    ],
    "case_003": [
        ("warfarin", "fluconazole", "contraindicated"),
        ("warfarin", "amiodarone", "major"),
    ],
    "case_004": [],
}

# Cases where a pharmacist escalation is the correct outcome.
SHOULD_ESCALATE = {"case_003"}
# Cases where the enhanced high-risk follow-up pathway should be applied.
SHOULD_ENHANCE = {"case_002", "case_004"}


def _packet_metrics(packet: dict[str, Any], stem: str) -> dict[str, Any]:
    """Score one produced packet against the seeded ground truth."""
    meds = packet.get("medications") or {}
    followup = packet.get("followup") or {}

    interactions = meds.get("interactions", []) or []
    found_pairs = {
        frozenset({(i.get("drug_a") or "").lower(), (i.get("drug_b") or "").lower()})
        for i in interactions
    }
    expected = SEEDED_CONFLICTS.get(stem, [])
    # Substring-tolerant match: models sometimes write "warfarin sodium".
    detected = 0
    for a, b, _ in expected:
        hit = any(
            any(a in name for name in pair) and any(b in name for name in pair)
            for pair in found_pairs
        )
        detected += 1 if hit else 0

    serious = [
        i for i in interactions
        if (i.get("severity") or "") in {"major", "contraindicated"}
    ]

    return {
        "artifacts_produced": sum(
            1 for k in ("summary", "medications", "followup", "education") if packet.get(k)
        ),
        "completeness": round(
            sum(1 for k in ("summary", "medications", "followup", "education")
                if packet.get(k)) / 4.0, 2
        ),
        "seeded_conflicts": len(expected),
        "conflicts_detected": detected,
        "conflict_recall": round(detected / len(expected), 2) if expected else None,
        "interactions_reported": len(interactions),
        "serious_interactions": len(serious),
        "escalated_for_pharmacist": bool(
            meds.get("pharmacist_review_required")
            or any("PHARMACIST" in e.upper() for e in packet.get("escalations", []))
        ),
        "escalation_expected": stem in SHOULD_ESCALATE,
        "enhanced_pathway": bool(followup.get("enhanced_pathway")),
        "enhanced_expected": stem in SHOULD_ENHANCE,
        "appointments": len(followup.get("appointments", []) or []),
        "unreconciled": len(meds.get("unreconciled", []) or []),
        "red_flags": len((packet.get("education") or {}).get("red_flag_symptoms", []) or []),
    }


def run_variant(stem: str, variant: str) -> dict[str, Any]:
    """Run one case through one orchestration variant."""
    case = load_case(CFG.samples_dir / f"{stem}.json")
    trace_id = f"compare_{stem}_{variant}"
    tracer = Tracer(trace_id, case_id=case.case_id)
    tracer.register_pii(case.patient.name, case.patient.mrn)

    tools, client = build_toolbox(tracer)
    state = new_state(
        case_id=f"{case.case_id}-{variant.upper()}",
        session_id="comparison",
        trace_id=trace_id,
        patient=case.patient,
        quarantined_notes=quarantine_all(case.clinical_notes, case.nurse_handoff_notes),
    )
    state["risk"] = assess_risk(case.patient)

    started = time.monotonic()
    try:
        if variant == "single":
            result = run_single_agent(state, tracer=tracer, tools=tools)
            metrics = result.get("_metrics", {})
            steps = 4
        else:
            memory = TieredMemory(
                episodic_path=CFG.state_dir / "comparison_memory.sqlite",
                semantic_path=CFG.state_dir / "comparison_chroma",
                tracer=tracer,
            )
            graph = build_graph(
                tracer,
                memory=memory,
                checkpointer=make_checkpointer(CFG.state_dir / "comparison_cp.sqlite"),
                with_interrupts=False,
                tools=tools,
            )
            result = graph.invoke(
                state,
                config={
                    "configurable": {"thread_id": state["case_id"]},
                    "recursion_limit": 60,
                },
            )
            metrics = {}
            steps = result.get("supervisor_steps", 0)
    except Exception as exc:
        return {"case": stem, "variant": variant, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if client:
            client.close()

    elapsed = round(time.monotonic() - started, 2)
    packet = result.get("packet", {}) or {}

    counts = tracer.counts()
    usage = tracer.usage_summary()
    row = {
        "case": stem,
        "variant": variant,
        "duration_seconds": elapsed,
        "steps": steps,
        # Real provider-reported tokens, not a call-count proxy. This is the metric
        # docs/single-vs-multi-agent.md claims to weigh, so it has to be measured.
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "estimated_cost_usd": usage["estimated_cost_usd"],
        "llm_calls": usage["llm_calls"],
        "tool_calls": counts.get("tool_call", 0),
        "rag_calls": counts.get("rag_query", 0),
        "schema_failures": counts.get("worker_failed", 0)
        + metrics.get("schema_failures", 0),
        "reflections": counts.get("reflection", 0),
        "routing_decisions": counts.get("routing_decision", 0),
        "context_chars": metrics.get("context_chars"),
        "status": result.get("status", "unknown"),
        **_packet_metrics(packet, stem),
    }
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-variant scores."""
    out: dict[str, Any] = {}
    for variant in ("single", "multi"):
        subset = [r for r in rows if r["variant"] == variant and "error" not in r]
        if not subset:
            out[variant] = {"runs": 0}
            continue
        recalls = [r["conflict_recall"] for r in subset if r["conflict_recall"] is not None]
        escalation_cases = [r for r in subset if r["escalation_expected"]]
        enhance_cases = [r for r in subset if r["enhanced_expected"]]
        no_escalation_cases = [r for r in subset if not r["escalation_expected"]]
        out[variant] = {
            "runs": len(subset),
            "mean_completeness": round(
                sum(r["completeness"] for r in subset) / len(subset), 3
            ),
            "schema_failures_total": sum(r["schema_failures"] for r in subset),
            "conflict_recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
            "correct_escalations": sum(
                1 for r in escalation_cases if r["escalated_for_pharmacist"]
            ),
            "escalations_expected": len(escalation_cases),
            "false_escalations": sum(
                1 for r in no_escalation_cases if r["escalated_for_pharmacist"]
            ),
            "enhanced_pathway_applied": sum(
                1 for r in enhance_cases if r["enhanced_pathway"]
            ),
            "enhanced_expected": len(enhance_cases),
            "mean_duration_seconds": round(
                sum(r["duration_seconds"] for r in subset) / len(subset), 2
            ),
            "total_tokens": sum(r["total_tokens"] for r in subset),
            "mean_tokens_per_case": round(
                sum(r["total_tokens"] for r in subset) / len(subset)
            ),
            "total_cost_usd": round(sum(r["estimated_cost_usd"] for r in subset), 6),
            "total_llm_calls": sum(r["llm_calls"] for r in subset),
            "total_tool_calls": sum(r["tool_calls"] for r in subset),
            "mean_red_flags": round(sum(r["red_flags"] for r in subset) / len(subset), 2),
        }
    return out


def render_markdown(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    """Render the results block injected into the decision document."""
    single, multi = summary.get("single", {}), summary.get("multi", {})

    def cmp_row(label: str, key: str, higher_is_better: bool, fmt: str = "{}") -> str:
        s, m = single.get(key), multi.get(key)
        if s is None or m is None:
            left = s if s is not None else "—"
            right = m if m is not None else "—"
            return f"| {label} | {left} | {right} | — |\n"
        if s == m:
            winner = "tie"
        elif (m > s) == higher_is_better:
            winner = "**multi**"
        else:
            winner = "**single**"
        return f"| {label} | {fmt.format(s)} | {fmt.format(m)} | {winner} |\n"

    md = [
        f"**Run:** {datetime.now(UTC).isoformat(timespec='seconds')} · "
        f"{single.get('runs', 0)} case(s) per variant · same model, same tools, same schemas.\n\n",
        "| metric | single-agent | multi-agent | better |\n|---|---|---|---|\n",
        cmp_row("Mean packet completeness", "mean_completeness", True, "{:.2f}"),
        cmp_row("Schema failures (total)", "schema_failures_total", False),
        cmp_row("Seeded-conflict recall", "conflict_recall", True, "{}"),
        cmp_row("Correct pharmacist escalations", "correct_escalations", True),
        cmp_row("False escalations", "false_escalations", False),
        cmp_row("Enhanced pathway applied when required", "enhanced_pathway_applied", True),
        cmp_row("Mean red-flag symptoms", "mean_red_flags", True, "{:.2f}"),
        cmp_row("Total tokens", "total_tokens", False, "{:,}"),
        cmp_row("Mean tokens per case", "mean_tokens_per_case", False, "{:,}"),
        cmp_row("Estimated cost (USD)", "total_cost_usd", False, "${:.4f}"),
        cmp_row("Mean wall-clock (s)", "mean_duration_seconds", False, "{:.2f}"),
        cmp_row("Total LLM calls", "total_llm_calls", False),
        cmp_row("Total tool calls", "total_tool_calls", False),
        "\n### Per-case detail\n\n",
        "| case | variant | complete | conflicts | escalated | enhanced "
        "| tokens | steps | secs |\n",
        "|---|---|---|---|---|---|---|---|---|\n",
    ]
    for row in rows:
        if "error" in row:
            md.append(
                f"| `{row['case']}` | {row['variant']} | ERROR: {row['error'][:60]} "
                "| | | | | | |\n"
            )
            continue
        conflicts = (
            f"{row['conflicts_detected']}/{row['seeded_conflicts']}"
            if row["seeded_conflicts"]
            else "—"
        )
        md.append(
            f"| `{row['case']}` | {row['variant']} | {row['completeness']:.2f} | {conflicts} "
            f"| {'yes' if row['escalated_for_pharmacist'] else 'no'}"
            f"{'' if row['escalation_expected'] == row['escalated_for_pharmacist'] else ' ⚠'} "
            f"| {'yes' if row['enhanced_pathway'] else 'no'}"
            f"{'' if row['enhanced_expected'] == row['enhanced_pathway'] else ' ⚠'} "
            f"| {row['total_tokens']:,} | {row['steps']} | {row['duration_seconds']:.1f} |\n"
        )

    md.append(
        "\n_⚠ marks a divergence from the expected outcome for that case._\n\n"
        "Raw data: [`../evidence/logs/comparison.json`](../evidence/logs/comparison.json)\n"
    )
    return "".join(md)


def inject_into_doc(markdown: str) -> None:
    """Replace the marked results block in the decision document."""
    doc_path = REPO_ROOT / "docs" / "single-vs-multi-agent.md"
    text = doc_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(<!-- BEGIN:COMPARISON_RESULTS -->\n).*?(\n<!-- END:COMPARISON_RESULTS -->)",
        re.DOTALL,
    )
    if not pattern.search(text):
        print("  WARNING: results markers not found in docs/single-vs-multi-agent.md")
        return
    doc_path.write_text(pattern.sub(rf"\1{markdown}\2", text), encoding="utf-8")
    print(f"  updated {doc_path.relative_to(REPO_ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases", nargs="+",
        default=["case_001", "case_002", "case_003", "case_004"],
    )
    args = parser.parse_args()

    CFG.ensure_dirs()
    if not CFG.google_api_key:
        print("GOOGLE_API_KEY is not set — the comparison needs live model calls.")
        return 1

    print("Single-agent vs multi-agent comparison")
    print(f"  cases: {', '.join(args.cases)}\n")

    rows: list[dict[str, Any]] = []
    for stem in args.cases:
        for variant in ("single", "multi"):
            print(f"  {stem} · {variant} …", flush=True)
            row = run_variant(stem, variant)
            rows.append(row)
            if "error" in row:
                print(f"      ERROR: {row['error'][:120]}")
            else:
                print(
                    f"      completeness {row['completeness']:.2f} · "
                    f"conflicts {row['conflicts_detected']}/{row['seeded_conflicts']} · "
                    f"{row['total_tokens']:,} tok · {row['duration_seconds']:.1f}s"
                )

    summary = summarize(rows)
    payload = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": CFG.model,
        "note": (
            "Both variants use the same model, tools, knowledge base and schemas. "
            "Only the orchestration differs."
        ),
        "ground_truth": {
            "seeded_conflicts": dict(SEEDED_CONFLICTS),
            "escalation_expected": sorted(SHOULD_ESCALATE),
            "enhanced_pathway_expected": sorted(SHOULD_ENHANCE),
        },
        "summary": summary,
        "runs": rows,
    }
    out_path = CFG.logs_dir / "comparison.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n  wrote {out_path.relative_to(REPO_ROOT)}")

    inject_into_doc(render_markdown(rows, summary))

    print("\nSummary")
    for variant in ("single", "multi"):
        s = summary.get(variant, {})
        if s.get("runs"):
            print(
                f"  {variant:6s}  completeness {s['mean_completeness']:.2f} · "
                f"conflict recall {s['conflict_recall']} · "
                f"{s['total_tokens']:,} tokens (${s['total_cost_usd']:.4f}) · "
                f"{s['mean_duration_seconds']:.1f}s mean"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
