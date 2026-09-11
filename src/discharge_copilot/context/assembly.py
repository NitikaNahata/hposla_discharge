"""Context assembly — the write / select / compress / isolate strategies (§7.4).

Each worker gets a *deliberately different* context. This module is where that decision lives, so
the four strategies are visible in one place rather than scattered through the node code:

* **WRITE**    — durable facts are written out of the conversation into memory tiers
                 (`memory/`), and the run's own scratch (`plan`, `completed`, `reflections`) is
                 written into typed state rather than re-derived from the transcript.
* **SELECT**   — `build_worker_context` picks only the state slices and recalled memories a given
                 worker needs. The medication worker never sees education guidance.
* **COMPRESS** — long threads are summarized by `nodes/compress.py`; the rolling summary is
                 injected here as `compressed_history` instead of the raw transcript.
* **ISOLATE**  — untrusted free-text is fenced by `context/quarantine.py`, and each worker runs in
                 its own context window so one worker's noise cannot contaminate another's.

`docs/context-engineering.md` maps each strategy to its implementation and evidence.
"""

from __future__ import annotations

from typing import Any

from ..schemas import PatientRecord
from ..state import DischargeState
from .quarantine import render_quarantined

# ---------------------------------------------------------------------------
# SELECT — per-worker context specifications
# ---------------------------------------------------------------------------

# What each worker is allowed to see. This is the isolation boundary in data form:
# adding a field here is a deliberate act, not an accident of prompt drift.
WORKER_CONTEXT_SPEC: dict[str, dict[str, Any]] = {
    "summary": {
        "patient_fields": [
            "age", "sex", "primary_diagnosis", "secondary_diagnoses", "problem_list",
            "admission_date", "discharge_date",
        ],
        "needs_notes": True,
        "needs_medications": False,
        "upstream": [],
        "memory_query": "prior admissions, hospital course, baseline condition",
    },
    "medication": {
        "patient_fields": [
            "age", "primary_diagnosis", "allergies",
            "pre_admission_medications", "discharge_medications",
        ],
        "needs_notes": True,
        "needs_medications": True,
        "upstream": [],
        "memory_query": "medication history, allergies, adverse drug reactions, adherence",
    },
    "followup": {
        "patient_fields": [
            "age", "primary_diagnosis", "secondary_diagnoses",
            "prior_admissions_12mo", "lives_alone", "mobility_limited",
        ],
        "needs_notes": False,
        "needs_medications": False,
        # The follow-up plan depends on what reconciliation found.
        "upstream": ["risk", "medications"],
        "memory_query": "missed appointments, preferred clinic, transport needs, caregiver",
    },
    "education": {
        "patient_fields": [
            "age", "primary_diagnosis", "primary_language", "caregiver",
            "lives_alone", "discharge_medications",
        ],
        "needs_notes": False,
        "needs_medications": True,
        # Education must reflect the final medication and follow-up decisions.
        "upstream": ["medications", "followup"],
        "memory_query": "health literacy, language preference, caregiver, education history",
    },
}


def _format_patient(patient: PatientRecord, fields: list[str]) -> str:
    """Render only the selected slice of the patient record."""
    lines: list[str] = []
    for field in fields:
        value = getattr(patient, field, None)
        if value in (None, "", [], False) and field not in {"lives_alone", "mobility_limited"}:
            continue
        if field in {"pre_admission_medications", "discharge_medications"}:
            if not value:
                continue
            rendered = "; ".join(m.label() for m in value) or "none"
            lines.append(f"- {field.replace('_', ' ')}: {rendered}")
        elif isinstance(value, list):
            lines.append(f"- {field.replace('_', ' ')}: {', '.join(str(v) for v in value)}")
        else:
            lines.append(f"- {field.replace('_', ' ')}: {value}")
    los = patient.length_of_stay_days
    if los is not None and "admission_date" in fields:
        lines.append(f"- length of stay: {los} days")
    return "\n".join(lines) if lines else "- (no relevant fields)"


def _format_upstream(state: DischargeState, keys: list[str]) -> str:
    """Render accepted upstream worker outputs the current worker depends on."""
    blocks: list[str] = []
    for key in keys:
        value = state.get(key)
        if value is None:
            continue
        payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        blocks.append(f"### {key}\n{_compact(payload)}")
    return "\n\n".join(blocks)


def _compact(payload: Any, indent: int = 0) -> str:
    """Compact, readable rendering of a nested structure — cheaper than JSON in tokens."""
    pad = "  " * indent
    if isinstance(payload, dict):
        out = []
        for k, v in payload.items():
            if v in (None, "", [], {}):
                continue
            if isinstance(v, (dict, list)):
                out.append(f"{pad}{k}:")
                out.append(_compact(v, indent + 1))
            else:
                out.append(f"{pad}{k}: {v}")
        return "\n".join(out)
    if isinstance(payload, list):
        return "\n".join(_compact(item, indent) for item in payload)
    return f"{pad}{payload}"


def _format_memory(state: DischargeState, limit: int = 5) -> str:
    """Render recalled long-term memories (SELECT from the memory tiers)."""
    hits = state.get("memory_hits") or []
    if not hits:
        return ""
    lines = ["Recalled from prior sessions (long-term memory):"]
    for hit in hits[:limit]:
        lines.append(
            f"- [{hit['tier']}] {hit['content']} "
            f"(importance {hit['importance']:.2f}, session {hit['session_id']})"
        )
    return "\n".join(lines)


def build_worker_context(state: DischargeState, worker: str) -> str:
    """Assemble the isolated context block for one worker.

    Returns the user-message body. The system prompt is supplied by the node itself, so the
    trust boundary is unambiguous: everything returned here is context, not instruction.
    """
    spec = WORKER_CONTEXT_SPEC[worker]
    patient: PatientRecord = state["patient"]

    sections: list[str] = []

    sections.append("## Patient (synthetic)\n" + _format_patient(patient, spec["patient_fields"]))

    risk = state.get("risk")
    if risk is not None and worker in {"followup", "education"}:
        sections.append(
            f"## Readmission risk\n- tier: {risk.tier.value}\n- score: {risk.score:.2f}\n"
            f"- factors: {', '.join(risk.factors) or 'none recorded'}"
        )

    upstream = _format_upstream(state, spec["upstream"])
    if upstream:
        sections.append("## Upstream findings you must be consistent with\n" + upstream)

    memory = _format_memory(state)
    if memory:
        sections.append("## Memory\n" + memory)

    # COMPRESS — inject the rolling summary rather than the full transcript.
    compressed = state.get("compressed_history") or ""
    if compressed:
        sections.append("## Earlier in this case (compressed)\n" + compressed)

    # ISOLATE — untrusted free-text always goes last, always fenced.
    if spec["needs_notes"]:
        notes = render_quarantined(state.get("quarantined_notes") or [])
        if notes:
            sections.append("## Untrusted clinical free-text\n" + notes)

    # Self-healing: the critic's issues from a prior attempt (AC-12).
    revision = (state.get("pending_revision") or {})
    if revision.get("worker") == worker and revision.get("issues"):
        issues = "\n".join(f"- {i}" for i in revision["issues"])
        sections.append(
            "## Revision required — your previous attempt was rejected\n"
            f"A reviewer found these defects. Fix every one of them:\n{issues}"
        )

    return "\n\n".join(sections)
