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
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from discharge_copilot.config import get_config
from discharge_copilot.schemas import (
    DischargeSummary,
    EducationPacket,
    FollowUpPlan,
    MedicationReconciliation,
)
from evaluation.agent_evaluator import generate_report


def load_case_data(case_id: str) -> dict:
    """Load synthetic case data from samples."""
    case_file = REPO_ROOT / "data" / "samples" / f"{case_id.lower().replace('-', '_')}.json"
    if not case_file.exists():
        raise FileNotFoundError(f"Case file not found: {case_file}")
    return json.loads(case_file.read_text())


def load_trace(case_id: str) -> dict | None:
    """Load execution trace for a case."""
    trace_file = REPO_ROOT / "evidence" / "traces" / f"{case_id.lower().replace('-', '_')}.jsonl"
    if not trace_file.exists():
        return None

    events = [
        json.loads(line)
        for line in trace_file.read_text().splitlines()
        if line.strip()
    ]
    return {"case_id": case_id, "events": events}


def load_artifacts(case_id: str) -> dict[str, Any]:
    """Load the real generated packet (not the trace, which only summarizes it)."""
    packet_file = REPO_ROOT / "evidence" / "packets" / f"{case_id.lower().replace('-', '_')}.json"
    if not packet_file.exists():
        return {}

    packet = json.loads(packet_file.read_text())
    artifacts: dict[str, Any] = {}
    if packet.get("medications"):
        artifacts["medications"] = MedicationReconciliation(**packet["medications"])
    if packet.get("followup"):
        artifacts["followup"] = FollowUpPlan(**packet["followup"])
    if packet.get("education"):
        artifacts["education"] = EducationPacket(**packet["education"])
    if packet.get("summary"):
        artifacts["summary"] = DischargeSummary(**packet["summary"])
    return artifacts


# Maps trace node names to the workstream they belong to. pharmacist_review and
# enhanced_followup are extensions of the medication/followup workstreams respectively,
# not separate ones — their tool calls should count toward that parent workstream.
NODE_TO_WORKER: dict[str, str] = {
    "medication": "medication",
    "pharmacist_review": "medication",
    "followup": "followup",
    "enhanced_followup": "followup",
    "education": "education",
    "summary": "summary",
}


def extract_tools_by_worker(trace: dict) -> dict[str, list[str]]:
    """Attribute each tool call to the worker active when it ran.

    Tool-call events carry no worker field of their own. Worker identity instead lives
    in node_enter's `node` field (e.g. node="medication") — this tracks the current
    worker as it scans the trace in order and attributes each tool call accordingly.
    """
    tools_by_worker: dict[str, list[str]] = {}
    current_worker: str | None = None

    for event in sorted(trace["events"], key=lambda e: e.get("seq", 0)):
        name = event.get("event")
        if name == "node_enter":
            current_worker = NODE_TO_WORKER.get(event.get("node"), current_worker)
        elif name == "tool_call" and current_worker:
            tools_by_worker.setdefault(current_worker, []).append(
                event.get("tool", "")
            )

    return tools_by_worker


def run_evaluation(
    case_id: str | None = None,
    json_output: bool = False,
    write_evidence: bool = False,
) -> None:
    """Run evaluation on case(s)."""
    cfg = get_config()

    if case_id:
        cases = [case_id.upper()]
    else:
        trace_dir = REPO_ROOT / "evidence" / "traces"
        cases = [
            p.stem.upper()
            for p in trace_dir.glob("case_*.jsonl")
            if not p.stem.endswith(("_pause", "_resume", "_faultinject"))
        ]

    results = []

    for case in cases:
        try:
            case_data = load_case_data(case)
            trace = load_trace(case)

            if not trace:
                print(f"⚠️  No trace found for {case}, skipping evaluation", file=sys.stderr)
                continue

            artifacts = load_artifacts(case)
            if not artifacts:
                print(
                    f"⚠️  No generated packet found for {case} "
                    f"(run `discharge_copilot run --case data/samples/{case.lower()}.json` "
                    f"first), skipping",
                    file=sys.stderr,
                )
                continue

            tools_by_worker = extract_tools_by_worker(trace)

            patient = case_data.get("patient", {})
            patient_context = {
                "case_id": case,
                "primary_diagnosis": patient.get("primary_diagnosis", ""),
                "risk_tier": "unknown",
                "discharge_medications": patient.get("discharge_medications", []),
            }

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
                print(f"\n{'=' * 70}")
                print(f"Case: {case}")
                print(f"Overall Score: {report['overall_score']} ({report['grade']})")
                print(f"{'=' * 70}")

                for worker, evals in report.get("workers", {}).items():
                    print(f"\n  {worker.upper()}:")
                    print(f"    Tool usage: {evals['tool_usage']['score']}")
                    print(f"    Answer quality: {evals['answer_quality']['score']}")
                    print(f"    Alignment: {evals['alignment']['alignment_score']}")

                    for issue in evals["answer_quality"]["issues"]:
                        print(f"      ⚠️  {issue}")

        except Exception as e:
            print(f"❌ Error evaluating {case}: {e}", file=sys.stderr)
            continue

    if results:
        avg_score = sum(r["overall_score"] for r in results) / len(results)
        print(f"\n{'=' * 70}")
        print(f"SUMMARY: {len(results)} cases evaluated")
        print(f"Average score: {avg_score:.2f}")
        print(f"{'=' * 70}\n")

    if write_evidence and results:
        evidence_file = cfg.evidence_dir / "agent_evaluation_report.json"
        evidence_file.write_text(json.dumps(results, indent=2))
        print(f"✅ Evaluation report written to {evidence_file}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate agent performance on committed traces"
    )
    parser.add_argument(
        "--case", type=str, help="Specific case to evaluate (e.g., CASE-001)"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
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
