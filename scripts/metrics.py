"""Aggregate operational metrics across committed traces.

    python scripts/metrics.py
    python scripts/metrics.py --json
    python scripts/metrics.py --write-evidence

The per-run traces are the raw telemetry; this turns them into the questions you actually want
answered across runs:

* Which node is slow, and how variable is it?
* How often does the critic reject each worker, and does it usually recover?
* What is the tool failure rate, by tool and by boundary (MCP vs in-process)?
* Which conditional branches have actually been exercised by committed evidence?
* What did the runs cost in tokens?

Reads only committed files, so it works on a clean clone with no API key and no running service.
That is the point: observability that requires a live backend produces nothing a static reviewer
can read.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from discharge_copilot.config import get_config

CFG = get_config()

# Branches that exist in the graph. Used to report coverage — a conditional edge no
# committed run has taken is untested in practice, whatever the unit tests say.
KNOWN_BRANCHES: dict[str, set[str]] = {
    "route_from_supervisor": {"summary", "medication", "followup", "education", "finalize"},
    "route_after_medication": {"pharmacist_review", "reflect"},
    "route_risk_tier": {"enhanced_followup", "reflect"},
    "route_after_reflection": {
        "summary", "medication", "followup", "education", "compress", "supervisor",
    },
}


def load_traces(directory: Path) -> dict[str, list[dict[str, Any]]]:
    traces: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(directory.glob("*.jsonl")):
        events = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if events:
            traces[path.stem] = events
    return traces


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(round(pct / 100 * (len(ordered) - 1)), len(ordered) - 1)
    return round(ordered[index], 1)


def compute(traces: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    node_durations: dict[str, list[float]] = defaultdict(list)
    routing: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    critic: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    confidences: dict[str, list[float]] = defaultdict(list)
    tools: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "failed": 0})
    tool_sources: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "failed": 0})
    schema: dict[str, dict[str, int]] = defaultdict(lambda: {"valid": 0, "invalid": 0})
    rag_queries: list[str] = []
    tokens = {"input": 0, "output": 0, "total": 0, "calls": 0, "reported": 0, "unreported": 0}
    tokens_by_node: dict[str, int] = defaultdict(int)
    run_rows: list[dict[str, Any]] = []

    for name, events in traces.items():
        run: dict[str, Any] = {
            "trace": name,
            "events": len(events),
            "duration_ms": events[-1].get("elapsed_ms", 0),
            "rag_calls": 0,
            "tool_failures": 0,
            "self_heals": 0,
            "status": "unknown",
            "total_tokens": 0,
        }
        for e in events:
            event = e["event"]

            if event == "node_exit" and "duration_ms" in e:
                node_durations[e["node"]].append(float(e["duration_ms"]))

            elif event == "routing_decision":
                routing[e["router"]][e["decision"]] += 1

            elif event == "reflection":
                critic[e["worker"]][e["action"]] += 1
                confidences[e["worker"]].append(float(e["confidence"]))
                if e["action"] == "revise":
                    run["self_heals"] += 1

            elif event == "tool_call":
                key = "ok" if e.get("ok") else "failed"
                tools[e["tool"]][key] += 1
                tool_sources[e.get("source", "unknown")][key] += 1
                if not e.get("ok"):
                    run["tool_failures"] += 1

            elif event == "structured_output":
                schema[e["node"]]["valid" if e.get("schema_valid") else "invalid"] += 1

            elif event == "rag_query":
                rag_queries.append(e["query"])
                run["rag_calls"] += 1

            elif event == "token_usage":
                if e.get("reported"):
                    tokens["reported"] += 1
                    tokens["input"] += int(e.get("input_tokens", 0))
                    tokens["output"] += int(e.get("output_tokens", 0))
                    total = int(e.get("total_tokens", 0))
                    tokens["total"] += total
                    tokens_by_node[e.get("node", "unknown")] += total
                    run["total_tokens"] += total
                else:
                    tokens["unreported"] += 1
                tokens["calls"] += 1

            elif event in {"run_complete", "packet_finalized"}:
                run["status"] = e.get("status", run["status"])

        run_rows.append(run)

    latency = {
        node: {
            "calls": len(values),
            "mean_ms": round(statistics.mean(values), 1),
            "median_ms": round(statistics.median(values), 1),
            "p95_ms": _percentile(values, 95),
            "max_ms": round(max(values), 1),
            "total_ms": round(sum(values), 1),
        }
        for node, values in sorted(
            node_durations.items(), key=lambda kv: -sum(kv[1])
        )
    }

    critic_summary = {}
    for worker, actions in critic.items():
        total = sum(actions.values())
        critic_summary[worker] = {
            "verdicts": total,
            "accept": actions.get("accept", 0),
            "revise": actions.get("revise", 0),
            "escalate": actions.get("escalate", 0),
            "rejection_rate": round(
                (actions.get("revise", 0) + actions.get("escalate", 0)) / total, 3
            )
            if total
            else 0.0,
            "mean_confidence": round(statistics.mean(confidences[worker]), 3)
            if confidences[worker]
            else 0.0,
        }

    branch_coverage = {}
    for router, expected in KNOWN_BRANCHES.items():
        taken = set(routing.get(router, {}))
        branch_coverage[router] = {
            "branches_taken": sorted(taken),
            "branches_untaken": sorted(expected - taken),
            "coverage": round(len(taken & expected) / len(expected), 2),
        }

    tool_total = sum(v["ok"] + v["failed"] for v in tools.values())
    tool_failed = sum(v["failed"] for v in tools.values())

    return {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "traces_analysed": len(traces),
        "runs": sorted(run_rows, key=lambda r: -r["duration_ms"]),
        "node_latency": latency,
        "routing": {r: dict(d) for r, d in routing.items()},
        "branch_coverage": branch_coverage,
        "critic": critic_summary,
        "tools": {
            "by_tool": {k: dict(v) for k, v in tools.items()},
            "by_source": {k: dict(v) for k, v in tool_sources.items()},
            "total_calls": tool_total,
            "failures": tool_failed,
            "failure_rate": round(tool_failed / tool_total, 4) if tool_total else 0.0,
        },
        "structured_output": {
            k: {**v, "validity_rate": round(v["valid"] / (v["valid"] + v["invalid"]), 3)}
            for k, v in schema.items()
            if (v["valid"] + v["invalid"])
        },
        "agentic_rag": {
            "total_queries": len(rag_queries),
            "distinct_queries": len(set(rag_queries)),
            "sample": rag_queries[:8],
        },
        "tokens": {
            **tokens,
            "by_node": dict(sorted(tokens_by_node.items(), key=lambda kv: -kv[1])),
            "note": (
                "Counts come from the provider's reported usage. Calls where usage was not "
                "reported are counted under 'unreported' rather than estimated."
            ),
        },
    }


def render(m: dict[str, Any]) -> str:
    out: list[str] = [
        "# Operational Metrics",
        "",
        f"**Generated:** {m['generated']}  ",
        f"**Traces analysed:** {m['traces_analysed']}  ",
        "**Regenerate:** `python scripts/metrics.py --write-evidence`",
        "",
        "Aggregated from committed traces in `evidence/traces/`. Reads files only — no live "
        "backend, no API key.",
        "",
        "---",
        "",
        "## Node latency",
        "",
        "| node | calls | mean | median | p95 | max | total |",
        "|---|---|---|---|---|---|---|",
    ]
    for node, s in m["node_latency"].items():
        out.append(
            f"| `{node}` | {s['calls']} | {s['mean_ms']:.0f}ms | {s['median_ms']:.0f}ms "
            f"| {s['p95_ms']:.0f}ms | {s['max_ms']:.0f}ms | {s['total_ms'] / 1000:.1f}s |"
        )

    out += ["", "## Conditional-branch coverage (AC-03)", ""]
    out.append(
        "A branch no committed run has taken is untested in practice, whatever the unit "
        "tests assert. This table reports which edges the evidence actually exercises."
    )
    out += ["", "| router | coverage | taken | not taken |", "|---|---|---|---|"]
    for router, c in m["branch_coverage"].items():
        untaken = ", ".join(f"`{b}`" for b in c["branches_untaken"]) or "—"
        taken = ", ".join(f"`{b}`" for b in c["branches_taken"]) or "—"
        out.append(f"| `{router}` | {c['coverage']:.0%} | {taken} | {untaken} |")

    out += ["", "## Critic verdicts (AC-12)", "",
            "| worker | verdicts | accept | revise | escalate | rejection rate | mean confidence |",
            "|---|---|---|---|---|---|---|"]
    for worker, c in m["critic"].items():
        out.append(
            f"| `{worker}` | {c['verdicts']} | {c['accept']} | {c['revise']} | "
            f"{c['escalate']} | {c['rejection_rate']:.0%} | {c['mean_confidence']:.2f} |"
        )

    tools = m["tools"]
    out += ["", "## Tool reliability", "",
            f"{tools['total_calls']} calls · {tools['failures']} failures "
            f"({tools['failure_rate']:.1%})", "",
            "| tool | ok | failed |", "|---|---|---|"]
    for tool, s in tools["by_tool"].items():
        out.append(f"| `{tool}` | {s['ok']} | {s['failed']} |")
    out += ["", "| boundary | ok | failed |", "|---|---|---|"]
    for source, s in tools["by_source"].items():
        out.append(f"| {source} | {s['ok']} | {s['failed']} |")

    out += ["", "## Structured-output validity (AC-04)", "",
            "| node | valid | invalid | validity rate |", "|---|---|---|---|"]
    for node, s in m["structured_output"].items():
        out.append(
            f"| `{node}` | {s['valid']} | {s['invalid']} | {s['validity_rate']:.0%} |"
        )

    t = m["tokens"]
    out += ["", "## Token usage", "",
            f"- **Total:** {t['total']:,} ({t['input']:,} in / {t['output']:,} out)",
            f"- **Model calls:** {t['calls']} "
            f"({t['reported']} reported usage, {t['unreported']} did not)", ""]
    if t["by_node"]:
        out += ["| node | tokens |", "|---|---|"]
        for node, count in list(t["by_node"].items())[:15]:
            out.append(f"| `{node}` | {count:,} |")
    out += ["", f"_{t['note']}_", ""]

    rag = m["agentic_rag"]
    out += ["## Agentic RAG (AC-11)", "",
            f"{rag['total_queries']} retrievals, {rag['distinct_queries']} distinct queries — "
            "all composed by the agent, none scheduled.", ""]
    for q in rag["sample"]:
        out.append(f"- _\"{q}\"_")

    out += ["", "## Per-run", "",
            "| trace | status | duration | tokens | RAG | tool failures | self-heals |",
            "|---|---|---|---|---|---|---|"]
    for r in m["runs"]:
        out.append(
            f"| `{r['trace']}` | {r['status']} | {r['duration_ms'] / 1000:.1f}s "
            f"| {r['total_tokens']:,} | {r['rag_calls']} | {r['tool_failures']} "
            f"| {r['self_heals']} |"
        )
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit raw JSON.")
    parser.add_argument(
        "--write-evidence", action="store_true",
        help="Write evidence/logs/observability.log and metrics.json.",
    )
    parser.add_argument("--traces", type=Path, default=CFG.traces_dir)
    args = parser.parse_args()

    traces = load_traces(args.traces)
    if not traces:
        print(f"No traces found in {args.traces}. Run ./run.sh first.")
        return 1

    metrics = compute(traces)

    if args.json:
        print(json.dumps(metrics, indent=2))
        return 0

    report = render(metrics)
    print(report)

    if args.write_evidence:
        CFG.ensure_dirs()
        (CFG.logs_dir / "observability.log").write_text(report, encoding="utf-8")
        (CFG.logs_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2), encoding="utf-8"
        )
        print(f"\nWrote {CFG.logs_dir / 'observability.log'}")
        print(f"Wrote {CFG.logs_dir / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
