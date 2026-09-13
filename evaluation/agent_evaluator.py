"""Agent evaluation suite — tool usage, answer quality, alignment (beyond capstone).

Evaluates discharge-planning agent on:
  1. Tool selection — did it call the right tools?
  2. Answer quality — grounding, completeness, safety
  3. Clinical alignment — does output match discharge context?

Usage:
    python -m evaluation.agent_evaluator --case data/samples/case_001.json
    python -m evaluation.agent_evaluator --json

This is INSTRUCTOR-REQUIRED evaluation (not part of capstone rubric).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# DeepEval imports
from deepeval.metrics import (
    ContextualRelevancy,
    Faithfulness,
    AnswerRelevancy,
)
from deepeval.test_case import LLMTestCase

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from discharge_copilot.schemas import (
    DischargeSummary,
    MedicationReconciliation,
    FollowUpPlan,
    EducationPacket,
)

# ============================================================================
# Evaluation Metrics
# ============================================================================


class ToolUsageEvaluator:
    """Did the agent call the right tools for this workstream?"""

    EXPECTED_TOOLS: dict[str, set[str]] = {
        "summary": {"patient_lookup", "search_clinical_guidance"},
        "medication": {
            "medication_interaction_check",
            "search_clinical_guidance",
        },
        "followup": {"schedule_followup", "search_clinical_guidance"},
        "education": {"search_clinical_guidance"},
    }

    @staticmethod
    def evaluate(worker: str, tools_called: list[str]) -> dict[str, Any]:
        """Score tool usage appropriateness.

        Returns:
            score (0-1): percentage of called tools that were expected
            correct_tools: tools called that were expected
            unexpected_tools: tools called that weren't expected
        """
        expected = ToolUsageEvaluator.EXPECTED_TOOLS.get(worker, set())
        called_set = set(tools_called)

        correct = called_set & expected
        unexpected = called_set - expected
        missed = expected - called_set

        if not expected:
            score = 1.0 if not called_set else 0.5
        else:
            # Correct tools + no unexpected = good
            # Missing expected tools = bad
            score = len(correct) / len(expected) if expected else 1.0
            if unexpected:
                score *= 0.7  # Penalty for unexpected tool use

        return {
            "worker": worker,
            "score": round(score, 2),
            "correct_tools": sorted(list(correct)),
            "unexpected_tools": sorted(list(unexpected)),
            "missed_tools": sorted(list(missed)),
            "rationale": (
                f"{len(correct)}/{len(expected)} expected tools called; "
                f"{len(unexpected)} unexpected calls"
            ),
        }


class AnswerQualityEvaluator:
    """Is the agent's output grounded, complete, and clinically safe?"""

    @staticmethod
    def evaluate_medication(
        artifact: MedicationReconciliation, patient_context: str
    ) -> dict[str, Any]:
        """Evaluate medication reconciliation quality."""
        issues: list[str] = []
        score = 1.0

        # Check 1: All medications accounted for
        if not artifact.changes and not artifact.unreconciled:
            issues.append("No medications accounted for")
            score -= 0.3

        # Check 2: Interactions documented
        if artifact.highest_severity in {"major", "contraindicated"} and not artifact.pharmacist_review_required:
            issues.append("Serious interaction not flagged for pharmacist review")
            score -= 0.2

        # Check 3: Unreconciled items explained
        if artifact.unreconciled and not artifact.rationale:
            issues.append("Unreconciled medications listed without explanation")
            score -= 0.1

        return {
            "worker": "medication",
            "schema": "MedicationReconciliation",
            "score": max(0.0, round(score, 2)),
            "issues": issues,
            "changes_count": len(artifact.changes),
            "interactions_count": len(artifact.interactions),
            "has_pharmacist_review": artifact.pharmacist_review_required,
        }

    @staticmethod
    def evaluate_followup(
        artifact: FollowUpPlan, risk_tier: str
    ) -> dict[str, Any]:
        """Evaluate follow-up plan quality and risk-appropriate routing."""
        issues: list[str] = []
        score = 1.0

        # Check 1: Appointments exist
        if not artifact.appointments:
            issues.append("No appointments scheduled")
            score -= 0.3

        # Check 2: Risk tier alignment
        if risk_tier == "high":
            if not artifact.enhanced_pathway:
                issues.append("HIGH risk but enhanced pathway not applied")
                score -= 0.2
            soon = [a for a in artifact.appointments if a.within_days <= 7]
            if not soon:
                issues.append("HIGH risk but no appointment within 7 days")
                score -= 0.2

        # Check 3: Home services for high-risk
        if risk_tier == "high" and not artifact.home_services:
            issues.append("HIGH risk patient but no home services offered")
            score -= 0.1

        return {
            "worker": "followup",
            "schema": "FollowUpPlan",
            "score": max(0.0, round(score, 2)),
            "issues": issues,
            "appointments_count": len(artifact.appointments),
            "enhanced_pathway": artifact.enhanced_pathway,
            "home_services_count": len(artifact.home_services),
            "risk_alignment": risk_tier == "high" and artifact.enhanced_pathway,
        }

    @staticmethod
    def evaluate_education(
        artifact: EducationPacket, has_discharge_meds: bool
    ) -> dict[str, Any]:
        """Evaluate patient education quality and safety."""
        issues: list[str] = []
        score = 1.0

        # Check 1: Red flags (CRITICAL for safety)
        if not artifact.red_flag_symptoms:
            issues.append("CRITICAL: No red-flag symptoms provided")
            score -= 0.4

        # Check 2: Medication instructions when meds exist
        if has_discharge_meds and not artifact.medication_instructions:
            issues.append("Patient has medications but no instructions")
            score -= 0.3

        # Check 3: Language/readability
        if not artifact.plain_language_summary or len(artifact.plain_language_summary) < 100:
            issues.append("Plain language summary too brief or missing")
            score -= 0.1

        return {
            "worker": "education",
            "schema": "EducationPacket",
            "score": max(0.0, round(score, 2)),
            "issues": issues,
            "red_flags_count": len(artifact.red_flag_symptoms),
            "med_instructions_count": len(artifact.medication_instructions),
            "language": artifact.language,
            "safety_critical_issue": not artifact.red_flag_symptoms,
        }

    @staticmethod
    def evaluate_summary(
        artifact: DischargeSummary, primary_diagnosis: str
    ) -> dict[str, Any]:
        """Evaluate discharge summary completeness."""
        issues: list[str] = []
        score = 1.0

        # Check 1: Clinical narrative present
        if not artifact.hospital_course or len(artifact.hospital_course) < 50:
            issues.append("Hospital course narrative missing or too brief")
            score -= 0.2

        # Check 2: Disposition specified
        if not artifact.disposition:
            issues.append("Disposition not specified")
            score -= 0.1

        # Check 3: Diagnosis grounding
        if artifact.primary_diagnosis.lower() != primary_diagnosis.lower():
            issues.append(
                f"Diagnosis mismatch: expected '{primary_diagnosis}', "
                f"got '{artifact.primary_diagnosis}'"
            )
            score -= 0.2

        return {
            "worker": "summary",
            "schema": "DischargeSummary",
            "score": max(0.0, round(score, 2)),
            "issues": issues,
            "diagnosis": artifact.primary_diagnosis,
            "has_hospital_course": bool(artifact.hospital_course),
            "disposition": artifact.disposition,
            "pending_results_count": len(artifact.pending_results),
        }


class AgentAlignmentEvaluator:
    """Does agent behavior align with discharge-planning context?"""

    @staticmethod
    def evaluate_tool_selection_appropriateness(
        worker: str, tools_called: list[str], context: str
    ) -> dict[str, Any]:
        """Score if tool selection matches context appropriateness."""
        # Define when tools SHOULD be called
        tool_triggers: dict[str, dict[str, str]] = {
            "medication": {
                "medication_interaction_check": "when discharge meds exist",
                "search_clinical_guidance": "when unsure about contraindication",
            },
            "followup": {
                "schedule_followup": "when appointment details needed",
                "search_clinical_guidance": "for follow-up interval lookup",
            },
            "education": {
                "search_clinical_guidance": "for patient-facing explanations",
            },
        }

        expected_for_context = tool_triggers.get(worker, {})
        called_set = set(tools_called)

        # Score: called tools that make sense for this worker
        alignment_score = sum(
            1 for tool in called_set if tool in expected_for_context
        ) / max(1, len(expected_for_context))

        return {
            "worker": worker,
            "alignment_score": round(alignment_score, 2),
            "tools_called": sorted(list(called_set)),
            "expected_triggers": expected_for_context,
            "note": "Score 1.0 = all tools called are contextually appropriate",
        }


# ============================================================================
# Evaluation Report
# ============================================================================


def generate_report(
    case_id: str,
    artifacts: dict[str, Any],
    tools_called: dict[str, list[str]],
    patient_context: dict[str, Any],
) -> dict[str, Any]:
    """Generate comprehensive agent evaluation report."""

    report = {
        "case_id": case_id,
        "evaluation_type": "agent_quality_and_alignment",
        "workers": {},
    }

    # Medication evaluation
    if "medications" in artifacts and artifacts["medications"]:
        med_artifact = artifacts["medications"]
        report["workers"]["medication"] = {
            "tool_usage": ToolUsageEvaluator.evaluate(
                "medication", tools_called.get("medication", [])
            ),
            "answer_quality": AnswerQualityEvaluator.evaluate_medication(
                med_artifact, str(patient_context)
            ),
            "alignment": AgentAlignmentEvaluator.evaluate_tool_selection_appropriateness(
                "medication", tools_called.get("medication", []), str(patient_context)
            ),
        }

    # Followup evaluation
    if "followup" in artifacts and artifacts["followup"]:
        followup_artifact = artifacts["followup"]
        risk_tier = patient_context.get("risk_tier", "unknown")
        report["workers"]["followup"] = {
            "tool_usage": ToolUsageEvaluator.evaluate(
                "followup", tools_called.get("followup", [])
            ),
            "answer_quality": AnswerQualityEvaluator.evaluate_followup(
                followup_artifact, risk_tier
            ),
            "alignment": AgentAlignmentEvaluator.evaluate_tool_selection_appropriateness(
                "followup", tools_called.get("followup", []), str(patient_context)
            ),
        }

    # Education evaluation
    if "education" in artifacts and artifacts["education"]:
        edu_artifact = artifacts["education"]
        has_meds = bool(patient_context.get("discharge_medications", []))
        report["workers"]["education"] = {
            "tool_usage": ToolUsageEvaluator.evaluate(
                "education", tools_called.get("education", [])
            ),
            "answer_quality": AnswerQualityEvaluator.evaluate_education(
                edu_artifact, has_meds
            ),
            "alignment": AgentAlignmentEvaluator.evaluate_tool_selection_appropriateness(
                "education", tools_called.get("education", []), str(patient_context)
            ),
        }

    # Summary evaluation
    if "summary" in artifacts and artifacts["summary"]:
        summary_artifact = artifacts["summary"]
        primary_dx = patient_context.get("primary_diagnosis", "")
        report["workers"]["summary"] = {
            "tool_usage": ToolUsageEvaluator.evaluate(
                "summary", tools_called.get("summary", [])
            ),
            "answer_quality": AnswerQualityEvaluator.evaluate_summary(
                summary_artifact, primary_dx
            ),
            "alignment": AgentAlignmentEvaluator.evaluate_tool_selection_appropriateness(
                "summary", tools_called.get("summary", []), str(patient_context)
            ),
        }

    # Overall score
    overall_scores = []
    for worker_eval in report["workers"].values():
        quality_score = worker_eval["answer_quality"]["score"]
        tool_score = worker_eval["tool_usage"]["score"]
        alignment_score = worker_eval["alignment"]["alignment_score"]
        avg = (quality_score + tool_score + alignment_score) / 3
        overall_scores.append(avg)

    report["overall_score"] = round(
        sum(overall_scores) / len(overall_scores), 2
    ) if overall_scores else 0.0
    report["grade"] = (
        "A" if report["overall_score"] >= 0.90
        else "B" if report["overall_score"] >= 0.80
        else "C" if report["overall_score"] >= 0.70
        else "D"
    )

    return report


if __name__ == "__main__":
    print("Agent evaluator module. Use with: evaluation/run_evaluation.py")
