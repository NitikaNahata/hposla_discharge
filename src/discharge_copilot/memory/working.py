"""Tier 1 — working memory (AC-06, NFR-08).

Short-term, in-state, scoped to the current run. This is the message history plus the run's own
scratch (plan, completed workstreams, reflections), all of which live in `DischargeState` and are
checkpointed with it.

Working memory is bounded. Left alone, a multi-turn case with four workers, tool observations and
self-healing retries grows a transcript that eventually crowds out the actual case data. The
window here keeps the most recent turns verbatim and hands everything older to the compression
middleware (`nodes/compress.py`), which is the COMPRESS half of the context strategy.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AnyMessage

from ..config import get_config
from ..llm import estimate_tokens


def window(messages: list[AnyMessage], size: int | None = None) -> list[AnyMessage]:
    """The most recent `size` messages — what stays verbatim in context."""
    limit = size or get_config().working_memory_window
    return messages[-limit:] if limit > 0 else list(messages)


def overflow(messages: list[AnyMessage], size: int | None = None) -> list[AnyMessage]:
    """Messages that have fallen out of the window and are candidates for compression."""
    limit = size or get_config().working_memory_window
    return messages[:-limit] if limit > 0 and len(messages) > limit else []


def render(messages: list[AnyMessage], *, max_chars: int = 400) -> str:
    """Render messages as plain text for summarization or transcript output."""
    lines: list[str] = []
    for message in messages:
        role = getattr(message, "type", "message")
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = " ".join(
                str(part.get("text", part)) if isinstance(part, dict) else str(part)
                for part in content
            )
        text = str(content).strip()
        if not text:
            # A tool-call turn carries no text; name the calls so the summary keeps the thread.
            calls = getattr(message, "tool_calls", None) or []
            if calls:
                text = f"(called tools: {', '.join(c.get('name', '?') for c in calls)})"
            else:
                continue
        if len(text) > max_chars:
            text = text[:max_chars] + " …"
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def token_estimate(messages: list[AnyMessage]) -> int:
    """Approximate token count of a message list (NFR-08 trigger)."""
    return estimate_tokens(render(messages, max_chars=100_000))


def stats(state: dict[str, Any]) -> dict[str, Any]:
    """Working-memory statistics, surfaced in traces and the Streamlit UI."""
    messages = state.get("messages", []) or []
    cfg = get_config()
    return {
        "messages": len(messages),
        "window_size": cfg.working_memory_window,
        "in_window": len(window(messages)),
        "overflow": len(overflow(messages)),
        "estimated_tokens": token_estimate(messages),
        "compression_trigger": cfg.compression_trigger_tokens,
        "compressed": bool(state.get("compressed_history")),
        "compression_events": len(state.get("compression_events", []) or []),
    }
