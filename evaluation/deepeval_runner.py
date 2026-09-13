#!/usr/bin/env python
"""DeepEval LLM-based evaluation using Gemini 3.1 Pro.

Evaluates agent outputs on:
  1. Faithfulness — Is output grounded in source context?
  2. AnswerRelevancy — Does output address the question?
  3. Hallucination — Did agent make up facts?
  4. ContextualRelevancy — Are retrieved docs relevant?

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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# DeepEval imports
from deepeval.metrics import (
    FaithfulnessMetric as Faithfulness,
    AnswerRelevancyMetric as AnswerRelevancy,
    HallucinationMetric as Hallucination,
    ContextualRelevancyMetric as ContextualRelevancy,
)
from deepeval.models import GeminiModel
from deepeval.test_case import LLMTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from discharge_copilot.schemas import (
    DischargeSummary,
    MedicationReconciliation,
    FollowUpPlan,
    EducationPacket,
)

# DeepEval's metrics default to an OpenAI model unless one is passed explicitly — this
# project only holds a Google API key, so every metric below is constructed with this
# GeminiModel instance instead of relying on a (nonexistent) DEEPEVAL_LLM_MODEL env var.
_GEMINI_JUDGE = GeminiModel(
    model="gemini-3.1-pro-preview",
    api_key=os.environ.get("GOOGLE_API_KEY"),
)

print("✅ Configured DeepEval to use Gemini 3.1 Pro (high-quality evaluations)")


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

    test_case = LLMTestCase(
        input=context,
        actual_output=json.dumps(
            artifact.model_dump(mode="json"), indent=2
        ),
        retrieval_context=[context],
    )

    try:
        faithfulness = Faithfulness(model=_GEMINI_JUDGE)
        faithfulness.measure(test_case)
        return {
            "metric": "Faithfulness",
            "worker": "medication",
            "score": faithfulness.score,
            "is_pass": faithfulness.success,
            "reasoning": faithfulness.reason,
        }
    except Exception as e:
        return {
            "metric": "Faithfulness",
            "worker": "medication",
            "score": 0.0,
            "is_pass": False,
            "error": str(e),
        }


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

    test_case = LLMTestCase(
        input=context,
        actual_output=json.dumps(
            artifact.model_dump(mode="json"), indent=2
        ),
        context=[context],
    )

    try:
        hallucination = Hallucination(model=_GEMINI_JUDGE)
        hallucination.measure(test_case)
        return {
            "metric": "Hallucination",
            "worker": "medication",
            "score": hallucination.score,
            "is_pass": hallucination.success,
            "reasoning": hallucination.reason,
        }
    except Exception as e:
        return {
            "metric": "Hallucination",
            "worker": "medication",
            "score": 0.0,
            "is_pass": False,
            "error": str(e),
        }


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

    test_case = LLMTestCase(
        input=context,
        actual_output=json.dumps(
            artifact.model_dump(mode="json"), indent=2
        ),
    )

    try:
        relevancy = AnswerRelevancy(model=_GEMINI_JUDGE)
        relevancy.measure(test_case)
        return {
            "metric": "AnswerRelevancy",
            "worker": "followup",
            "score": relevancy.score,
            "is_pass": relevancy.success,
            "reasoning": relevancy.reason,
        }
    except Exception as e:
        return {
            "metric": "AnswerRelevancy",
            "worker": "followup",
            "score": 0.0,
            "is_pass": False,
            "error": str(e),
        }


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

    test_case = LLMTestCase(
        input=context,
        actual_output=json.dumps(
            artifact.model_dump(mode="json"), indent=2
        ),
    )

    try:
        relevancy = AnswerRelevancy(model=_GEMINI_JUDGE)
        relevancy.measure(test_case)
        return {
            "metric": "AnswerRelevancy",
            "worker": "education",
            "score": relevancy.score,
            "is_pass": relevancy.success,
            "reasoning": relevancy.reason,
        }
    except Exception as e:
        return {
            "metric": "AnswerRelevancy",
            "worker": "education",
            "score": 0.0,
            "is_pass": False,
            "error": str(e),
        }


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

    test_case = LLMTestCase(
        input=context,
        actual_output=json.dumps(
            artifact.model_dump(mode="json"), indent=2
        ),
        retrieval_context=[context],
    )

    try:
        faithfulness = Faithfulness(model=_GEMINI_JUDGE)
        faithfulness.measure(test_case)
        return {
            "metric": "Faithfulness",
            "worker": "summary",
            "score": faithfulness.score,
            "is_pass": faithfulness.success,
            "reasoning": faithfulness.reason,
        }
    except Exception as e:
        return {
            "metric": "Faithfulness",
            "worker": "summary",
            "score": 0.0,
            "is_pass": False,
            "error": str(e),
        }


# ============================================================================
# Main Evaluation
# ============================================================================


def load_case_data(case_id: str) -> dict:
    """Load synthetic case data."""
    case_file = REPO_ROOT / "data" / "samples" / f"{case_id.lower()}.json"
    if not case_file.exists():
        raise FileNotFoundError(f"Case file not found: {case_file}")

    with open(case_file) as f:
        return json.load(f)


def load_packet_data(case_id: str) -> dict:
    """Load the finalized packet (real generated artifacts) written by `discharge_copilot run`.

    Traces only carry text summaries, not full artifact content, so the CLI persists the
    finalized packet to evidence/packets/<case>.json for evaluation to consume.
    """
    packet_file = REPO_ROOT / "evidence" / "packets" / f"{case_id.lower()}.json"
    if not packet_file.exists():
        return {}
    with open(packet_file) as f:
        return json.load(f)


def run_deepeval(case_id: str = None) -> dict[str, Any]:
    """Run DeepEval on committed cases."""
    print(f"\n{'='*70}")
    print(f"DeepEval Assessment (Gemini 3.1 Pro)")
    print(f"{'='*70}\n")

    # Determine cases
    if case_id:
        cases = [case_id.upper()]
    else:
        trace_dir = REPO_ROOT / "evidence" / "traces"
        cases = sorted([
            p.stem.upper()
            for p in trace_dir.glob("case_*.jsonl")
            if not p.stem.endswith("_pause")
            and not p.stem.endswith("_resume")
            and not p.stem.endswith("_faultinject")
        ])

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": "gemini-3.1-pro-preview",
        "cases": {},
    }

    for case in cases:
        try:
            print(f"📋 Evaluating {case}...")
            case_data = load_case_data(case)
            patient = case_data.get("patient", {})
            clinical_notes = "\n".join(case_data.get("clinical_notes", []))

            patient_context = {
                "case_id": case,
                "primary_diagnosis": patient.get("primary_diagnosis", ""),
                "risk_tier": "unknown",  # Would read from trace
                "discharge_medications": patient.get("discharge_medications", []),
                "pre_admission_medications": patient.get(
                    "pre_admission_medications", []
                ),
                "clinical_notes": clinical_notes,
                "medication_changes": [],  # Would read from trace
            }

            packet = load_packet_data(case)
            if not packet:
                print(
                    f"  ⚠️  No generated packet found for {case} "
                    f"(run `discharge_copilot run --case data/samples/{case.lower()}.json` first) — skipping"
                )
                results["cases"][case] = {
                    "case_id": case,
                    "error": "no packet found; run the case before evaluating it",
                }
                continue

            case_results = {"case_id": case, "evaluations": []}

            # Medication evaluations
            print(f"  🩺 Evaluating medication faithfulness...")
            med_faith = evaluate_medication_faithfulness(
                MedicationReconciliation(**packet.get("medications", {})),
                patient_context,
            )
            case_results["evaluations"].append(med_faith)

            print(f"  🩺 Evaluating medication hallucination...")
            med_halluc = evaluate_medication_hallucination(
                MedicationReconciliation(**packet.get("medications", {})),
                patient_context,
            )
            case_results["evaluations"].append(med_halluc)

            print(f"  📅 Evaluating followup relevancy...")
            followup_rel = evaluate_followup_relevancy(
                FollowUpPlan(**packet.get("followup", {})),
                patient_context,
            )
            case_results["evaluations"].append(followup_rel)

            print(f"  📖 Evaluating education relevancy...")
            edu_rel = evaluate_education_relevancy(
                EducationPacket(**packet.get("education", {})),
                patient_context,
            )
            case_results["evaluations"].append(edu_rel)

            print(f"  📝 Evaluating summary faithfulness...")
            summary_faith = evaluate_summary_faithfulness(
                DischargeSummary(**packet.get("summary", {})),
                patient_context,
            )
            case_results["evaluations"].append(summary_faith)

            # Aggregate scores
            scores = [e["score"] for e in case_results["evaluations"] if "score" in e]
            case_results["overall_score"] = (
                sum(scores) / len(scores) if scores else 0.0
            )
            case_results["pass_rate"] = sum(
                1 for e in case_results["evaluations"] if e.get("is_pass")
            ) / len(case_results["evaluations"])

            results["cases"][case] = case_results
            print(f"  ✅ {case}: Score {case_results['overall_score']:.2f}\n")

        except Exception as e:
            print(f"  ❌ Error: {e}\n")
            results["cases"][case] = {"error": str(e)}

    # Overall summary
    valid_scores = [
        c["overall_score"]
        for c in results["cases"].values()
        if "overall_score" in c
    ]
    if valid_scores:
        results["average_score"] = sum(valid_scores) / len(valid_scores)
        results["grade"] = (
            "A"
            if results["average_score"] >= 0.90
            else "B"
            if results["average_score"] >= 0.80
            else "C"
            if results["average_score"] >= 0.70
            else "D"
        )

    return results


def commit_results(results: dict[str, Any], commit: bool = False):
    """Write results to evidence and optionally commit to git."""
    evidence_file = REPO_ROOT / "evidence" / "deepeval_report.json"
    evidence_file.write_text(json.dumps(results, indent=2))
    print(f"\n✅ DeepEval report written to: {evidence_file}")

    if commit:
        import subprocess

        try:
            # Add to git
            subprocess.run(
                ["git", "add", str(evidence_file)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )

            # Commit
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"docs: add DeepEval assessment report (Gemini 3.1 Pro)\n\n"
                    f"Average score: {results.get('average_score', 0):.2f}\n"
                    f"Model: {results.get('model')}\n"
                    f"Timestamp: {results.get('timestamp')}",
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            print(f"✅ Results committed to git!")
        except subprocess.CalledProcessError as e:
            print(f"⚠️  Git commit failed: {e.stderr.decode()}")


def main():
    parser = argparse.ArgumentParser(
        description="DeepEval LLM-based evaluation using Gemini 3.1 Pro"
    )
    parser.add_argument(
        "--case",
        type=str,
        help="Specific case to evaluate (e.g., CASE-001)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Commit results to git after evaluation",
    )

    args = parser.parse_args()

    results = run_deepeval(case_id=args.case)
    commit_results(results, commit=args.commit)

    print(f"\n{'='*70}")
    print(f"Average Score: {results.get('average_score', 0):.2f}")
    print(f"Grade: {results.get('grade', 'N/A')}")
    print(f"Model: {results.get('model')}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
