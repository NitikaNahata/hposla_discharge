"""Shared test fixtures.

Tests are split into two kinds:

* **Offline** (the default) — exercise typed state, schemas, routers, the MCP server, memory and
  quarantine with no network and no API key. These are the tests that back the deterministically
  scored rubric parameters, so they must pass on a clean clone with no credentials.
* **Live** (`@pytest.mark.live`) — make real Gemini calls. Skipped automatically when
  `GOOGLE_API_KEY` is unset.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from discharge_copilot.config import REPO_ROOT
from discharge_copilot.context.quarantine import quarantine_all
from discharge_copilot.schemas import DischargeCase
from discharge_copilot.state import new_state

SAMPLES = REPO_ROOT / "data" / "samples"


def pytest_collection_modifyitems(config, items):
    """Skip live tests when no API key is configured."""
    if os.getenv("GOOGLE_API_KEY", "").strip():
        return
    skip = pytest.mark.skip(reason="GOOGLE_API_KEY not set — live Gemini tests skipped")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def load_sample(name: str) -> DischargeCase:
    """Load a committed synthetic case by filename stem."""
    raw = json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    return DischargeCase.model_validate(raw)


@pytest.fixture
def case_low_risk() -> DischargeCase:
    """CASE-001 — low risk, clean reconciliation."""
    return load_sample("case_001")


@pytest.fixture
def case_high_risk() -> DischargeCase:
    """CASE-002 — high readmission risk."""
    return load_sample("case_002")


@pytest.fixture
def case_interactions() -> DischargeCase:
    """CASE-003 — seeded contraindicated + major interactions."""
    return load_sample("case_003")


@pytest.fixture
def case_injection() -> DischargeCase:
    """CASE-004 — carries a prompt-injection attempt, and is a session-2 readmission."""
    return load_sample("case_004")


@pytest.fixture
def state_factory():
    """Build a graph state from a case, exactly as the CLI does."""

    def _build(case: DischargeCase, **overrides):
        state = new_state(
            case_id=case.case_id,
            session_id=case.session_id,
            trace_id=f"test_{case.case_id.lower()}",
            patient=case.patient,
            quarantined_notes=quarantine_all(
                case.clinical_notes, case.nurse_handoff_notes
            ),
        )
        state.update(overrides)
        return state

    return _build


@pytest.fixture
def tmp_tracer(tmp_path: Path):
    """A tracer writing to a temporary file, so tests never pollute evidence/."""
    from discharge_copilot.tracing import Tracer

    return Tracer("test_trace", case_id="TEST", path=tmp_path / "trace.jsonl")
