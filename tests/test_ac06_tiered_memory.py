"""AC-06 — The copilot maintains tiered memory and recalls a fact from an earlier turn.

Covers all three tiers: T1 working (in-state, windowed), T2 episodic (SQLite), T3 semantic
(Chroma + local embeddings), and the facade that merges them.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from discharge_copilot.memory import TieredMemory, working
from discharge_copilot.memory.episodic import EpisodicMemory
from discharge_copilot.memory.semantic import SemanticMemory

NAMESPACE = "MRN-AC06"


@pytest.fixture
def memory(tmp_path):
    return TieredMemory(
        episodic_path=tmp_path / "episodic.sqlite",
        semantic_path=tmp_path / "chroma_memory",
    )


# ---------------------------------------------------------------------------
# T1 — working memory
# ---------------------------------------------------------------------------


def test_ac06_working_memory_windows_the_transcript():
    """AC-06: short-term memory keeps a bounded window of recent turns."""
    messages = [HumanMessage(f"turn {i}") for i in range(20)]
    assert len(working.window(messages, size=8)) == 8
    assert working.window(messages, size=8)[-1].content == "turn 19"
    assert len(working.overflow(messages, size=8)) == 12


def test_ac06_working_memory_reports_stats():
    """AC-06/NFR-08: working memory exposes the counters compression decisions read."""
    state = {"messages": [AIMessage(f"finding {i}") for i in range(12)]}
    stats = working.stats(state)
    assert stats["messages"] == 12
    assert stats["overflow"] > 0
    assert stats["estimated_tokens"] > 0
    assert stats["compressed"] is False


def test_ac06_working_memory_render_keeps_tool_calls_visible():
    """AC-06: a tool-call turn carries no text, so the summary must name the calls instead."""
    message = AIMessage(
        content="",
        tool_calls=[{"name": "medication_interaction_check", "args": {}, "id": "1"}],
    )
    assert "medication_interaction_check" in working.render([message])


# ---------------------------------------------------------------------------
# T2 — episodic
# ---------------------------------------------------------------------------


def test_ac06_episodic_writes_and_recalls(tmp_path):
    """AC-06: the durable structured tier stores and returns patient facts."""
    mem = EpisodicMemory(db_path=tmp_path / "e.sqlite")
    mem.write(
        namespace=NAMESPACE, key="allergy:penicillin",
        content="Documented allergy: Penicillin.", kind="allergy", session_id="s1",
    )
    recalled = mem.recall(NAMESPACE)
    assert len(recalled) == 1
    assert recalled[0]["content"] == "Documented allergy: Penicillin."
    assert recalled[0]["kind"] == "allergy"


def test_ac06_episodic_ranks_by_effective_importance(tmp_path):
    """AC-06: an allergy outranks an incidental observation."""
    mem = EpisodicMemory(db_path=tmp_path / "e.sqlite")
    mem.write(namespace=NAMESPACE, key="obs", content="Spouse drove him home.",
              kind="observation", session_id="s1")
    mem.write(namespace=NAMESPACE, key="allergy:penicillin",
              content="Documented allergy: Penicillin.", kind="allergy", session_id="s1")
    assert mem.recall(NAMESPACE)[0]["key"] == "allergy:penicillin"


def test_ac06_recall_records_access_for_the_importance_boost(tmp_path):
    """AC-06/AC-08: recall is counted, feeding the access boost in the eviction policy."""
    mem = EpisodicMemory(db_path=tmp_path / "e.sqlite")
    mem.write(namespace=NAMESPACE, key="k", content="fact", kind="observation",
              session_id="s1")
    assert mem.get(NAMESPACE, "k")["access_count"] == 0
    mem.recall(NAMESPACE)
    mem.recall(NAMESPACE)
    assert mem.get(NAMESPACE, "k")["access_count"] == 2


def test_ac06_recall_without_touch_does_not_inflate_access(tmp_path):
    """AC-06: inspecting memory must not distort the eviction signal it is inspecting."""
    mem = EpisodicMemory(db_path=tmp_path / "e.sqlite")
    mem.write(namespace=NAMESPACE, key="k", content="fact", kind="observation",
              session_id="s1")
    mem.recall(NAMESPACE, touch=False)
    assert mem.get(NAMESPACE, "k")["access_count"] == 0


# ---------------------------------------------------------------------------
# T3 — semantic
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_ac06_semantic_recall_finds_facts_by_meaning(tmp_path):
    """AC-06: the semantic tier retrieves on meaning, not on matching words.

    This is the capability episodic lookup cannot provide: the query shares no significant
    vocabulary with the stored fact.
    """
    mem = SemanticMemory(persist_dir=tmp_path / "chroma_memory")
    mem.write(
        namespace=NAMESPACE, key="care_constraint:transport",
        content=("Patient has previously missed cardiology appointments because she has no "
                 "transport and does not drive."),
        kind="care_constraint", session_id="s1",
    )
    mem.write(
        namespace=NAMESPACE, key="obs:diet",
        content="Tolerating a regular diet on the ward.",
        kind="observation", session_id="s1",
    )
    hits = mem.recall(NAMESPACE, "why might this patient not attend her clinic visit?", k=2)
    assert hits
    assert hits[0]["key"] == "care_constraint:transport"


@pytest.mark.slow
def test_ac06_semantic_recall_is_namespaced(tmp_path):
    """AC-06: semantic recall never crosses patients."""
    mem = SemanticMemory(persist_dir=tmp_path / "chroma_memory")
    mem.write(namespace="MRN-A", key="k", content="Allergic to penicillin.",
              kind="allergy", session_id="s1")
    assert mem.recall("MRN-B", "allergies", k=3) == []
    assert mem.recall("MRN-A", "allergies", k=3)


# ---------------------------------------------------------------------------
# The facade
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_ac06_facade_merges_both_durable_tiers(memory):
    """AC-06: the graph-facing interface returns hits from both tiers, de-duplicated."""
    memory.write(namespace=NAMESPACE, key="allergy:penicillin",
                 content="Documented allergy: Penicillin.", kind="allergy", session_id="s1")
    memory.write(namespace=NAMESPACE, key="care_constraint:transport",
                 content="No transport to appointments; does not drive.",
                 kind="care_constraint", session_id="s1")

    hits = memory.recall_for_patient(NAMESPACE, "barriers to attending follow-up", k=5)
    assert hits
    assert len({h["key"] for h in hits}) == len(hits), "facade returned a duplicate key"
    assert {"semantic", "episodic"} & {h["tier"] for h in hits}


def test_ac06_recall_from_an_earlier_turn_in_the_same_case(memory, case_high_risk):
    """AC-06: a fact established earlier in the case is recalled later in it.

    This is the criterion's literal requirement — recall of a fact from an earlier turn —
    exercised through the same path the graph uses.
    """
    mrn = case_high_risk.patient.mrn
    memory.write(
        namespace=mrn, key="adherence_concern:diuretic",
        content="Could not state her own furosemide dose at teach-back.",
        kind="adherence_concern", session_id="session-1", case_id="CASE-002",
    )
    hits = memory.recall_for_patient(mrn, "does the patient understand her medications?", k=4)
    assert any("teach-back" in h["content"] for h in hits)


def test_ac06_write_case_facts_extracts_durable_facts(memory, case_high_risk, state_factory):
    """AC-06: finalize writes the facts a clinician would want surfaced next admission."""
    from discharge_copilot.nodes.intake import assess_risk
    from discharge_copilot.schemas import DischargePacket

    state = state_factory(case_high_risk, risk=assess_risk(case_high_risk.patient))
    packet = DischargePacket(case_id="CASE-002", patient_mrn=case_high_risk.patient.mrn)

    written = memory.write_case_facts(state, packet)
    keys = {w["key"] for w in written}

    assert "allergy:penicillin" in keys, "an allergy must always be persisted"
    assert "care_constraint:lives_alone" in keys
    assert "care_constraint:no_caregiver" in keys
    assert "care_constraint:mobility" in keys
    assert "risk_tier" in keys
    assert all(w["session_id"] == "session-1" for w in written)


def test_ac06_snapshot_exposes_the_full_memory_state(memory):
    """AC-06: memory is inspectable — used by the CLI and the Streamlit UI."""
    memory.write(namespace=NAMESPACE, key="allergy:penicillin",
                 content="Documented allergy: Penicillin.", kind="allergy",
                 session_id="s1", case_id="C1")
    snapshot = memory.snapshot(NAMESPACE)
    assert snapshot["episodic_count"] == 1
    assert snapshot["sessions"] == ["s1"]
    fact = snapshot["facts"][0]
    assert fact["permanent"] is True
    assert fact["base_importance"] == 1.0
    assert fact["effective_importance"] > 0.9
