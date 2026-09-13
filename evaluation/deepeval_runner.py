#!/usr/bin/env python
"""DeepEval LLM-based evaluation using Gemini 3.1 Pro.

Evaluates agent outputs on:
  1. Faithfulness — Is output grounded in source context?
  2. AnswerRelevancy — Does output address the question?
  3. Hallucination — Did agent make up facts?

Uses Gemini 3.1 Pro for high-quality evaluations (more accurate than Flash).

Usage:
    python evaluation/deepeval_runner.py
    python evaluation/deepeval_runner.py --case CASE-001
    python evaluation/deepeval_runner.py --commit

Commits results to: evidence/deepeval_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from deepeval.metrics import (
    AnswerRelevancyMetric as AnswerRelevancy,
)
from deepeval.metrics import (
    FaithfulnessMetric as Faithfulness,
)
from deepeval.metrics import (
    HallucinationMetric as Hallucination,
)
from deepeval.models import GeminiModel
from deepeval.test_case import LLMTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from discharge_copilot.schemas import (
    DischargeSummary,
    EducationPacket,
    FollowUpPlan,
    MedicationReconciliation,
)
from evaluation.grading import grade_for

JUDGE_MODEL = "gemini-3.1-pro-preview"


@lru_cache(maxsize=1)
def _get_judge() -> GeminiModel:
    """Build (once, lazily) the Gemini judge every metric is scored against.

    DeepEval's metrics default to an OpenAI model unless one is passed explicitly, and
    this project only holds a Google API key — there is no such thing as a
    DEEPEVAL_LLM_MODEL env var that DeepEval reads, so every metric must be constructed
    with this GeminiModel instance directly.
    """
    return GeminiModel(model=JUDGE_MODEL, api_key=os.environ.get("GOOGLE_API_KEY"))


def _measure(
    metric_cls: type,
    metric_name: str,
    worker: str,
    *,
    input_text: str,
    actual_output: str,
    retrieval_context: list[str] | None = None,
    context: list[str] | None = None,
) -> dict[str, Any]:
    """Run one DeepEval metric and normalize its result to a plain dict.

    Centralizes the LLMTestCase/metric-instance plumbing that every evaluate_* function
    below needs — DeepEval reports the score via attributes on the metric instance
    (.score/.reason/.success), not via .measure()'s return value.
    """
    test_case = LLMTestCase(
        input=input_text,
        actual_output=actual_output,
        retrieval_context=retrieval_context,
        context=context,
    )
    try:
        metric = metric_cls(model=_get_judge())
        metric.measure(test_case)
        return {
            "metric": metric_name,
            "worker": worker,
            "score": metric.score,
            "is_pass": metric.success,
            "reasoning": metric.reason,
        }
    except Exception as e:
        return {
            "metric": metric_name,
            "worker": worker,
            "score": 0.0,
            "is_pass": False,
            "error": str(e),
        }


# ============================================================================
# Evaluation Functions
# ============================================================================


def evaluate_medication_faithfulness(
    artifact: MedicationReconciliation, patient_context: dict
) -> dict[str, Any]:
    """Evaluate: Is medication reconciliation grounded in source data?"""
    clinical_notes = patient_context.get("clinical_notes", "")
    pre_admission_meds = patient_context.get("pre_admission_medications", [])

    context = f"""
Patient pre-admission medications:
{json.dumps(pre_admission_meds, indent=2)}

Clinical notes mentioning medications:
{clinical_notes}

Reconciliation must account for ALL pre-admission medications.
"""
    return _measure(
        Faithfulness,
        "Faithfulness",
        "medication",
        input_text=context,
        actual_output=json.dumps(artifact.model_dump(mode="json"), indent=2),
        retrieval_context=[context],
    )


def evaluate_medication_hallucination(
    artifact: MedicationReconciliation, patient_context: dict
) -> dict[str, Any]:
    """Evaluate: Did agent invent any medication data?"""
    known_medications = patient_context.get("pre_admission_medications", [])

    context = f"""
Known medications in patient record:
{json.dumps(known_medications, indent=2)}

Agent reconciliation output:
{json.dumps(artifact.model_dump(mode="json"), indent=2)}

Check: Are all medications in the reconciliation from the known list?
Do not hallucinate drug names, dosages, or interactions not in source.
"""
    return _measure(
        Hallucination,
        "Hallucination",
        "medication",
        input_text=context,
        actual_output=json.dumps(artifact.model_dump(mode="json"), indent=2),
        context=[context],
    )


def evaluate_followup_relevancy(
    artifact: FollowUpPlan, patient_context: dict
) -> dict[str, Any]:
    """Evaluate: Do followup appointments address clinical needs?"""
    primary_diagnosis = patient_context.get("primary_diagnosis", "")
    risk_tier = patient_context.get("risk_tier", "")
    medication_changes = patient_context.get("medication_changes", [])

    context = f"""
Patient diagnosis: {primary_diagnosis}
Readmission risk tier: {risk_tier}
Medication changes: {json.dumps(medication_changes, indent=2)}

Followup plan must address:
1. Primary diagnosis monitoring needs
2. Medication change monitoring (if any new drugs)
3. Risk-appropriate appointment timing

Follow-up appointments scheduled:
{json.dumps(artifact.model_dump(mode="json"), indent=2)}

Question: Do these appointments adequately address the patient's discharge needs?
"""
    return _measure(
        AnswerRelevancy,
        "AnswerRelevancy",
        "followup",
        input_text=context,
        actual_output=json.dumps(artifact.model_dump(mode="json"), indent=2),
    )


def evaluate_education_relevancy(
    artifact: EducationPacket, patient_context: dict
) -> dict[str, Any]:
    """Evaluate: Does education packet answer 'what should I do?'"""
    discharge_medications = patient_context.get("discharge_medications", [])

    context = f"""
Patient discharge medications:
{json.dumps(discharge_medications, indent=2)}

Patient education packet:
{json.dumps(artifact.model_dump(mode="json"), indent=2)}

Question: Does this education packet adequately answer:
1. "How should I take my medications?"
2. "What symptoms should I watch for?"
3. "When should I seek help?"

The packet must include specific, actionable red-flag symptoms (not vague descriptions).
"""
    return _measure(
        AnswerRelevancy,
        "AnswerRelevancy",
        "education",
        input_text=context,
        actual_output=json.dumps(artifact.model_dump(mode="json"), indent=2),
    )


def evaluate_summary_faithfulness(
    artifact: DischargeSummary, patient_context: dict
) -> dict[str, Any]:
    """Evaluate: Is discharge summary grounded in clinical facts?"""
    clinical_notes = patient_context.get("clinical_notes", "")
    primary_diagnosis = patient_context.get("primary_diagnosis", "")

    context = f"""
Source clinical notes:
{clinical_notes}

Expected primary diagnosis: {primary_diagnosis}

Discharge summary provided:
{json.dumps(artifact.model_dump(mode="json"), indent=2)}

Check: Is every statement in the summary grounded in the clinical notes?
Do not allow invented clinical findings, test results, or events.
"""
    return _measure(
        Faithfulness,
        "Faithfulness",
        "summary",
        input_text=context,
        actual_output=json.dumps(artifact.model_dump(mode="json"), indent=2),
        retrieval_context=[context],
    )


# ============================================================================
# Main Evaluation
# ============================================================================


def load_case_data(case_id: str) -> dict:
    """Load synthetic case data."""
    case_file = REPO_ROOT / "data" / "samples" / f"{case_id.lower().replace('-', '_')}.json"
    if not case_file.exists():
        raise FileNotFoundError(f"Case file not found: {case_file}")
    return json.loads(case_file.read_text())


def load_packet_data(case_id: str) -> dict:
    """Load the finalized packet (real generated artifacts) written by `discharge_copilot run`.

    Traces only carry text summaries, not full artifact content, so the CLI persists the
    finalized packet to evidence/packets/<case>.json for evaluation to consume.
    """
    packet_file = REPO_ROOT / "evidence" / "packets" / f"{case_id.lower().replace('-', '_')}.json"
    if not packet_file.exists():
        return {}
    return json.loads(packet_file.read_text())


def _discover_cases() -> list[str]:
    trace_dir = REPO_ROOT / "evidence" / "traces"
    return sorted(
        p.stem.upper()
        for p in trace_dir.glob("case_*.jsonl")
        if not p.stem.endswith(("_pause", "_resume", "_faultinject"))
    )


def _evaluate_case(case: str) -> dict[str, Any]:
    case_data = load_case_data(case)
    patient = case_data.get("patient", {})
    patient_context = {
        "case_id": case,
        "primary_diagnosis": patient.get("primary_diagnosis", ""),
        "discharge_medications": patient.get("discharge_medications", []),
        "pre_admission_medications": patient.get("pre_admission_medications", []),
        "clinical_notes": "\n".join(case_data.get("clinical_notes", [])),
    }

    packet = load_packet_data(case)
    if not packet:
        return {
            "case_id": case,
            "error": (
                f"no packet found; run `discharge_copilot run "
                f"--case data/samples/{case.lower()}.json` before evaluating it"
            ),
        }

    medications = MedicationReconciliation(**packet.get("medications", {}))
    evaluations = [
        evaluate_medication_faithfulness(medications, patient_context),
        evaluate_medication_hallucination(medications, patient_context),
        evaluate_followup_relevancy(
            FollowUpPlan(**packet.get("followup", {})), patient_context
        ),
        evaluate_education_relevancy(
            EducationPacket(**packet.get("education", {})), patient_context
        ),
        evaluate_summary_faithfulness(
            DischargeSummary(**packet.get("summary", {})), patient_context
        ),
    ]

    scores = [e["score"] for e in evaluations if "score" in e]
    overall_score = sum(scores) / len(scores) if scores else 0.0
    pass_rate = sum(1 for e in evaluations if e.get("is_pass")) / len(evaluations)

    return {
        "case_id": case,
        "evaluations": evaluations,
        "overall_score": overall_score,
        "pass_rate": pass_rate,
    }


def run_deepeval(case_id: str | None = None) -> dict[str, Any]:
    """Run DeepEval on committed cases."""
    print(f"\n{'=' * 70}")
    print(f"DeepEval Assessment ({JUDGE_MODEL})")
    print(f"{'=' * 70}\n")

    cases = [case_id.upper()] if case_id else _discover_cases()

    results: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "model": JUDGE_MODEL,
        "cases": {},
    }

    for case in cases:
        print(f"📋 Evaluating {case}...")
        try:
            case_result = _evaluate_case(case)
        except Exception as e:
            print(f"  ❌ Error: {e}\n")
            results["cases"][case] = {"case_id": case, "error": str(e)}
            continue

        results["cases"][case] = case_result
        if "overall_score" in case_result:
            print(f"  ✅ {case}: Score {case_result['overall_score']:.2f}\n")
        else:
            print(f"  ⚠️  {case_result['error']}\n")

    valid_scores = [
        c["overall_score"] for c in results["cases"].values() if "overall_score" in c
    ]
    if valid_scores:
        average_score = sum(valid_scores) / len(valid_scores)
        results["average_score"] = average_score
        results["grade"] = grade_for(average_score)

    return results


def commit_results(results: dict[str, Any], commit: bool = False) -> None:
    """Write results to evidence and optionally commit to git."""
    evidence_file = REPO_ROOT / "evidence" / "deepeval_report.json"
    evidence_file.write_text(json.dumps(results, indent=2))
    print(f"\n✅ DeepEval report written to: {evidence_file}")

    if not commit:
        return

    try:
        subprocess.run(
            ["git", "add", str(evidence_file)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"docs: add DeepEval assessment report ({JUDGE_MODEL})\n\n"
                f"Average score: {results.get('average_score', 0):.2f}\n"
                f"Model: {results.get('model')}\n"
                f"Timestamp: {results.get('timestamp')}",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        print("✅ Results committed to git!")
    except subprocess.CalledProcessError as e:
        print(f"⚠️  Git commit failed: {e.stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"DeepEval LLM-based evaluation using {JUDGE_MODEL}"
    )
    parser.add_argument(
        "--case", type=str, help="Specific case to evaluate (e.g., CASE-001)"
    )
    parser.add_argument(
        "--commit", action="store_true", help="Commit results to git after evaluation"
    )
    args = parser.parse_args()

    results = run_deepeval(case_id=args.case)
    commit_results(results, commit=args.commit)

    print(f"\n{'=' * 70}")
    print(f"Average Score: {results.get('average_score', 0):.2f}")
    print(f"Grade: {results.get('grade', 'N/A')}")
    print(f"Model: {results.get('model')}")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
