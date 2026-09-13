#!/usr/bin/env python
"""Run agent evaluation on committed traces.

Usage:
    python evaluation/run_evaluation.py
    python evaluation/run_evaluation.py --case CASE-001
    python evaluation/run_evaluation.py --json
    python evaluation/run_evaluation.py --write-evidence

Reads committed traces and generates evaluation report:
  - Tool usage appropriateness
  - Answer quality (grounding, completeness, safety)
  - Clinical alignment
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from discharge_copilot.config import get_config
from evaluation.agent_evaluator import (
    ToolUsageEvaluator,
    AnswerQualityEvaluator,
    AgentAlignmentEvaluator,
    generate_report,
)


def load_case_data(case_id: str) -> dict:
    """Load synthetic case data from samples."""
    case_file = REPO_ROOT / "data" / "samples" / f"{case_id.lower()}.json"
    if not case_file.exists():
        raise FileNotFoundError(f"Case file not found: {case_file}")

    import json as json_module
    with open(case_file) as f:
        data = json_module.load(f)
    return data


def load_trace(case_id: str) -> dict | None:
    """Load execution trace for a case."""
    trace_file = REPO_ROOT / "evidence" / "traces" / f"{case_id.lower()}.jsonl"
    if not trace_file.exists():
        return None

    events = []
    with open(trace_file) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return {"case_id": case_id, "events": events}


def extract_artifacts_and_tools(trace: dict) -> tuple[dict, dict]:
    """Extract produced artifacts and tool calls from trace."""
    artifacts = {}
    tools_by_worker = {}

    for event in trace["events"]:
        # Capture worker outputs
        if event.get("event") == "worker_output":
            worker = event.get("worker")
            # In real implementation, reconstruct from state snapshots
            tools_by_worker.setdefault(worker, [])

        # Capture tool calls
        if event.get("event") == "tool":
            worker = event.get("worker", "unknown")
            tool_name = event.get("tool")
            tools_by_worker.setdefault(worker, []).append(tool_name)

        # Capture structured outputs
        if event.get("event") == "structured_output":
            node = event.get("node")
            schema = event.get("schema")

    return artifacts, tools_by_worker


def run_evaluation(case_id: str = None, json_output: bool = False, write_evidence: bool = False):
    """Run evaluation on case(s)."""
    cfg = get_config()

    # Determine which cases to evaluate
    if case_id:
        cases = [case_id.upper()]
    else:
        # All committed cases
        trace_dir = REPO_ROOT / "evidence" / "traces"
        cases = [
            p.stem.upper()
            for p in trace_dir.glob("case_*.jsonl")
            if not p.stem.endswith("_pause") and not p.stem.endswith("_resume")
        ]

    results = []

    for case in cases:
        try:
            case_data = load_case_data(case)
            trace = load_trace(case)

            if not trace:
                print(f"⚠️  No trace found for {case}, skipping evaluation", file=sys.stderr)
                continue

            # Extract artifacts and tool calls
            artifacts, tools_by_worker = extract_artifacts_and_tools(trace)

            # Prepare context
            patient = case_data.get("patient", {})
            patient_context = {
                "case_id": case,
                "primary_diagnosis": patient.get("primary_diagnosis", ""),
                "risk_tier": "unknown",  # Would read from trace
                "discharge_medications": patient.get("discharge_medications", []),
            }

            # Generate report
            report = generate_report(
                case_id=case,
                artifacts=artifacts,
                tools_called=tools_by_worker,
                patient_context=patient_context,
            )

            results.append(report)

            if json_output:
                print(json.dumps(report, indent=2))
            else:
                print(f"\n{'='*70}")
                print(f"Case: {case}")
                print(f"Overall Score: {report['overall_score']} ({report['grade']})")
                print(f"{'='*70}")

                for worker, evals in report.get("workers", {}).items():
                    print(f"\n  {worker.upper()}:")
                    print(f"    Tool usage: {evals['tool_usage']['score']}")
                    print(f"    Answer quality: {evals['answer_quality']['score']}")
                    print(f"    Alignment: {evals['alignment']['alignment_score']}")

                    if evals['answer_quality']['issues']:
                        for issue in evals['answer_quality']['issues']:
                            print(f"      ⚠️  {issue}")

        except Exception as e:
            print(f"❌ Error evaluating {case}: {e}", file=sys.stderr)
            continue

    # Summary
    if results:
        avg_score = sum(r['overall_score'] for r in results) / len(results)
        print(f"\n{'='*70}")
        print(f"SUMMARY: {len(results)} cases evaluated")
        print(f"Average score: {avg_score:.2f}")
        print(f"{'='*70}\n")

    if write_evidence and results:
        evidence_file = REPO_ROOT / "evidence" / "agent_evaluation_report.json"
        evidence_file.write_text(json.dumps(results, indent=2))
        print(f"✅ Evaluation report written to {evidence_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate agent performance on committed traces"
    )
    parser.add_argument(
        "--case",
        type=str,
        help="Specific case to evaluate (e.g., CASE-001)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--write-evidence",
        action="store_true",
        help="Write report to evidence/agent_evaluation_report.json",
    )

    args = parser.parse_args()

    run_evaluation(
        case_id=args.case,
        json_output=args.json,
        write_evidence=args.write_evidence,
    )


if __name__ == "__main__":
    main()
