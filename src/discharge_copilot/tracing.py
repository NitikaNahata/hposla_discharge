"""Structured JSONL run traces with PII redaction (NFR-04, NFR-05).

Every node entry/exit, routing decision, tool call, memory operation and reflection is appended
to `evidence/traces/<trace_id>.jsonl`. These files are committed — they are the primary evidence
for AC-03, AC-11, AC-12 and NFR-04.

All data in this project is synthetic, but synthetic PII is still hashed before it reaches a log
(NFR-05): patient names and MRNs are replaced with a salted, truncated SHA-256 digest. The digest
is stable within a run, so a trace remains readable and correlatable without carrying identifiers
in plaintext.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import get_config
from .observability import TokenUsage, estimate_cost_usd

# Salt is per-process: digests correlate within a run, not across committed artifacts.
_RUN_SALT = uuid.uuid4().hex[:8]

# MRN-shaped identifiers in free text, e.g. MRN-2001.
_MRN_PATTERN = re.compile(r"\bMRN[-_]?\d{3,}\b", re.IGNORECASE)

# Very short strings are too collision-prone to redact by substring:
# replacing every "Li" would corrupt unrelated text while protecting nothing.
_MIN_REDACTABLE_LENGTH = 2

# Envelope fields omitted from the console echo — in the file, just screen noise.
_ECHO_SUPPRESSED_KEYS = frozenset(
    {"seq", "ts", "elapsed_ms", "trace_id", "case_id", "event"}
)


def pseudonymize(value: str, *, prefix: str = "id") -> str:
    """Stable, non-reversible pseudonym for a synthetic identifier (NFR-05)."""
    digest = hashlib.sha256(f"{_RUN_SALT}:{value}".encode()).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _redact_text(text: str, secrets: set[str]) -> str:
    """Replace known identifiers and MRN-shaped strings in a block of text."""
    for secret in secrets:
        if secret and len(secret) > _MIN_REDACTABLE_LENGTH and secret in text:
            text = text.replace(secret, pseudonymize(secret, prefix="pii"))
    return _MRN_PATTERN.sub(lambda m: pseudonymize(m.group(0), prefix="mrn"), text)


def _redact(value: Any, secrets: set[str]) -> Any:
    """Recursively redact identifiers from an arbitrary JSON-able structure."""
    if isinstance(value, str):
        return _redact_text(value, secrets)
    if isinstance(value, dict):
        return {k: _redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v, secrets) for v in value]
    return value


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of Pydantic models, dates and enums to JSON-able values."""
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class Tracer:
    """Append-only JSONL tracer for one graph run."""

    def __init__(
        self,
        trace_id: str,
        *,
        case_id: str = "",
        path: Path | None = None,
        echo: bool = False,
    ) -> None:
        cfg = get_config()
        cfg.ensure_dirs()
        self.trace_id = trace_id
        self.case_id = case_id
        self.path = path or (cfg.traces_dir / f"{trace_id}.jsonl")
        self.echo = echo
        self.redact = cfg.redact_pii
        self._secrets: set[str] = set()
        self._seq = 0
        self._started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        # Token accounting lives on the tracer so it lands in the committed trace,
        # where the comparison harness and the metrics command can read it.
        self.usage = TokenUsage()
        self.model = cfg.model
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Truncation is deferred to the first emit() (below), not done here: a Tracer is
        # sometimes constructed just to build a graph/toolbox for a checkpoint status
        # check, and a run that turns out to need no new work must never destroy the
        # existing trace from whichever run actually did the work.
        self._file_initialized = False

    # -- PII registration ---------------------------------------------------

    def register_pii(self, *values: str) -> None:
        """Register synthetic identifiers to be redacted from every subsequent event."""
        for v in values:
            if v:
                self._secrets.add(v)

    # -- Core emit ----------------------------------------------------------

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        """Append one structured event to the trace."""
        if not self._file_initialized:
            # Truncate on the first real write: a trace file describes exactly one run.
            self.path.write_text("", encoding="utf-8")
            self._file_initialized = True
        self._seq += 1
        record: dict[str, Any] = {
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "elapsed_ms": round((time.monotonic() - self._started) * 1000, 1),
            "trace_id": self.trace_id,
            "case_id": self.case_id,
            "event": event,
        }
        record.update(_jsonable(fields))
        if self.redact:
            record = _redact(record, self._secrets)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.events.append(record)
        if self.echo:
            body = {
                k: v for k, v in record.items() if k not in _ECHO_SUPPRESSED_KEYS
            }
            print(f"  [{record['event']}] " + json.dumps(body)[:180])
        return record

    # -- Convenience emitters ------------------------------------------------

    @contextmanager
    def node(self, name: str, **fields: Any) -> Iterator[dict[str, Any]]:
        """Trace a node's entry and exit, including failures."""
        started = time.monotonic()
        self.emit("node_enter", node=name, **fields)
        carrier: dict[str, Any] = {}
        try:
            yield carrier
        except Exception as exc:
            self.emit(
                "node_error",
                node=name,
                error_type=type(exc).__name__,
                error=str(exc),
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
            raise
        else:
            self.emit(
                "node_exit",
                node=name,
                duration_ms=round((time.monotonic() - started) * 1000, 1),
                **carrier,
            )

    def routing(self, router: str, decision: str, reason: str, **fields: Any) -> None:
        """Record a conditional-edge decision — the AC-03 evidence trail."""
        self.emit("routing_decision", router=router, decision=decision, reason=reason, **fields)

    def tool_call(self, tool: str, source: str, args: Any, result: Any, ok: bool = True) -> None:
        """Record a tool invocation. `source` is 'mcp' or 'local' (AC-10, AC-11)."""
        self.emit(
            "tool_call",
            tool=tool,
            source=source,
            args=args,
            ok=ok,
            result_preview=str(_jsonable(result))[:600],
        )

    def structured_output(self, node: str, schema: str, ok: bool, error: str = "") -> None:
        """Record schema validation at a handoff boundary (AC-04)."""
        self.emit(
            "structured_output", node=node, schema=schema, schema_valid=ok, error=error
        )

    def token_usage(self, node: str, usage: dict[str, int]) -> None:
        """Record one model call's token usage.

        Providers do not always report usage. An unreported call is traced with
        `reported: false` rather than estimated — an invented number in an evidence
        artifact is worse than a missing one.
        """
        if usage:
            self.usage.add(node, usage)
            self.emit("token_usage", node=node, reported=True, **usage)
        else:
            self.emit("token_usage", node=node, reported=False)

    def usage_summary(self) -> dict[str, Any]:
        """Run totals, written at run_complete and read by scripts/metrics."""
        summary = self.usage.as_dict()
        summary["model"] = self.model
        summary["estimated_cost_usd"] = estimate_cost_usd(self.usage, self.model)
        return summary

    def memory_op(self, op: str, tier: str, **fields: Any) -> None:
        """Record a memory read or write (AC-06..AC-08)."""
        self.emit("memory_op", op=op, tier=tier, **fields)

    # -- Summary -------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        """Event-type histogram, used by the transcript renderers."""
        out: dict[str, int] = {}
        for e in self.events:
            out[e["event"]] = out.get(e["event"], 0) + 1
        return out


def new_trace_id(case_id: str, suffix: str = "") -> str:
    """Deterministic-ish trace id: readable in a directory listing, unique enough per run."""
    base = case_id.lower().replace("-", "_")
    return f"{base}_{suffix}" if suffix else base
