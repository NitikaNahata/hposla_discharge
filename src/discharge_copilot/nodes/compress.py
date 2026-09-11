"""Summarization / compression middleware (NFR-08, §7.4 COMPRESS).

A discharge case with four workers, tool observations and self-healing retries accumulates a
long transcript. Left unmanaged it eventually crowds out the case data itself — the classic
context-window failure, where the agent knows a great deal about what it has been doing and less
and less about the patient.

This middleware watches working memory and, once the transcript passes a token threshold,
replaces everything outside the recent window with a running summary. Recent turns stay verbatim
because they are what the next step actually reasons over; older turns become a compressed record
of decisions and findings.

**What is deliberately preserved.** The summarizer is instructed to keep concrete clinical facts,
decisions and unresolved issues, and to drop process narration. Compressing "the medication
worker found a contraindicated warfarin-fluconazole interaction" into "medication review was
performed" would be a safety regression, not a saving.

Before/after token counts are recorded on every compression event, which is the NFR-08 evidence
in `evidence/logs/nfr08_compression.log`.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from pydantic import BaseModel, Field

from ..config import get_config
from ..llm import LLMFailure, estimate_tokens, invoke_structured
from ..memory import working
from ..state import DischargeState

COMPRESSION_SYSTEM = """You compress the working transcript of a hospital discharge-planning
agent so it fits in a bounded context window.

Preserve, always:
  - concrete clinical findings and the artifacts produced (diagnoses, medication decisions,
    interactions found and their severity, appointments booked)
  - decisions taken and the reason for each
  - unresolved issues, open questions, and anything flagged for human review
  - tool results that changed a decision

Discard:
  - process narration ("the supervisor then dispatched to...")
  - restatements of the same fact
  - conversational filler

Write in compact clinical shorthand, not prose. Never invent anything that is not in the
transcript, and never soften a safety finding to save space — losing "contraindicated
warfarin-fluconazole interaction" to make room is a patient-safety failure, not a compression
win."""


class CompressedHistory(BaseModel):
    """Structured output of one compression pass."""

    summary: str = Field(
        min_length=20, description="Compact record of decisions, findings and open issues."
    )
    key_findings: list[str] = Field(
        default_factory=list, description="Clinical findings that must not be lost."
    )
    open_issues: list[str] = Field(
        default_factory=list, description="Unresolved items carried forward."
    )

    def render(self) -> str:
        parts = [self.summary]
        if self.key_findings:
            parts.append(
                "Key findings: " + "; ".join(self.key_findings)
            )
        if self.open_issues:
            parts.append("Open issues: " + "; ".join(self.open_issues))
        return "\n".join(parts)


def should_compress(state: DischargeState) -> bool:
    """Whether the transcript has grown past the compression threshold (pure function)."""
    cfg = get_config()
    messages = state.get("messages", []) or []
    if len(messages) <= cfg.working_memory_window:
        return False
    return working.token_estimate(messages) >= cfg.compression_trigger_tokens


def make_compress_node(tracer: Any):
    """Build the compression middleware node."""
    cfg = get_config()

    def compress(state: DischargeState) -> dict[str, Any]:
        messages = state.get("messages", []) or []
        overflow = working.overflow(messages)
        if not overflow:
            return {}

        before_tokens = working.token_estimate(messages)
        transcript = working.render(overflow, max_chars=1500)
        existing = state.get("compressed_history", "")

        prompt = (
            (f"Existing summary of earlier turns:\n{existing}\n\n" if existing else "")
            + f"New transcript turns to fold in:\n{transcript}\n\n"
            "Produce a single consolidated summary covering both."
        )

        with tracer.node("compress") as carrier:
            try:
                result = invoke_structured(
                    [SystemMessage(COMPRESSION_SYSTEM), HumanMessage(prompt)],
                    CompressedHistory,
                    tracer=tracer,
                    node="compress",
                )
                summary = result.render()
            except LLMFailure as exc:
                # Degrade to truncation rather than losing the thread entirely (NFR-07).
                tracer.emit("compression_fallback", error=str(exc)[:200])
                summary = (existing + "\n" + transcript)[-2000:]
                result = CompressedHistory(summary=summary)

            after_tokens = estimate_tokens(summary) + working.token_estimate(
                working.window(messages)
            )
            event = {
                "messages_compressed": len(overflow),
                "messages_retained": len(messages) - len(overflow),
                "tokens_before": before_tokens,
                "tokens_after": after_tokens,
                "reduction_pct": (
                    round(100 * (1 - after_tokens / before_tokens), 1)
                    if before_tokens
                    else 0.0
                ),
                "key_findings_preserved": len(result.key_findings),
                "open_issues_preserved": len(result.open_issues),
                "trigger_tokens": cfg.compression_trigger_tokens,
            }
            tracer.emit("compression", **event)
            carrier.update(**event)

            return {
                "compressed_history": summary,
                "compression_events": [event],
                # Drop the compressed turns from the channel; the summary replaces them.
                "messages": [RemoveMessage(id=m.id) for m in overflow if m.id],
            }

    return compress
