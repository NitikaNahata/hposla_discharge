"""AC-11 — An agentic-RAG tool the agent decides when to call.

> retrieval inside the loop, not a fixed step

The distinction this file has to defend is between retrieval that is *available* and retrieval
that is *scheduled*. Two structural properties establish it:

1. `search_clinical_guidance` is a bound tool, not a graph node. Nothing in the topology forces
   it to run — `test_ac11_retrieval_is_not_a_graph_node` asserts that directly.
2. Whether it fires varies by case. `evidence/logs/ac11_rag_decisions.log` records a run where
   the agent called it and a run where it declined.
"""

from __future__ import annotations

import pytest

from discharge_copilot.tools.rag import (
    ClinicalGuidanceIndex,
    chunk_document,
    corpus_fingerprint,
    load_corpus,
    make_rag_tool,
)


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    """A real index over the committed corpus, built once for the module."""
    idx = ClinicalGuidanceIndex(persist_dir=tmp_path_factory.mktemp("chroma"))
    idx.build()
    return idx


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


def test_ac11_corpus_covers_both_required_lookup_domains():
    """AC-11: the criterion names discharge protocols AND medication guidance."""
    chunks = load_corpus()
    assert chunks
    doc_types = {c["metadata"]["doc_type"] for c in chunks}
    assert "discharge_protocol" in doc_types
    assert "medication_guidance" in doc_types


def test_ac11_chunking_splits_on_semantic_boundaries():
    """AC-11: sections are chunked on headings, which are real topic boundaries."""
    doc = (
        "# Title\n\nIntro paragraph that is long enough to be retained in the corpus.\n\n"
        "## Follow-up intervals\n\nSee the clinic within seven days of discharge for review.\n\n"
        "## Red flags\n\nReturn immediately for chest pain or new breathlessness at rest.\n"
    )
    chunks = chunk_document(doc, source="test.md", meta={"title": "Test"})
    headings = [c["metadata"]["heading"] for c in chunks]
    assert "Follow-up intervals" in headings
    assert "Red flags" in headings


def test_ac11_short_fragments_are_dropped():
    """AC-11: sub-60-character fragments are noise and are not indexed."""
    assert chunk_document("## Tiny\n\nno.\n", source="t.md", meta={}) == []


def test_ac11_fingerprint_changes_with_the_corpus():
    """AC-11: the index rebuilds when the knowledge base changes rather than going stale."""
    base = [{"text": "alpha content here", "metadata": {}}]
    changed = [{"text": "beta content here", "metadata": {}}]
    assert corpus_fingerprint(base) != corpus_fingerprint(changed)
    assert corpus_fingerprint(base) == corpus_fingerprint(list(base))


# ---------------------------------------------------------------------------
# Retrieval quality
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize(
    "query,expected_source",
    [
        ("warfarin interaction with fluconazole at discharge", "anticoagulation_guidance.md"),
        ("how soon to see a high risk heart failure patient", "hf_discharge_protocol.md"),
        ("why must NSAIDs be stopped in heart failure", "nsaid_renal_cardiac_risk.md"),
        ("pneumonia follow-up chest x-ray timing", "pneumonia_discharge_protocol.md"),
        ("what reading level for patient instructions", "patient_education_principles.md"),
        (
            "must every pre-admission medication be accounted for",
            "medication_reconciliation_standards.md",
        ),
    ],
)
def test_ac11_retrieval_returns_the_right_document(index, query, expected_source):
    """AC-11: retrieval is accurate enough to be worth the agent's call."""
    hits = index.search(query, k=3)
    assert hits
    assert expected_source in {h["source"] for h in hits}, (
        f"'{query}' did not retrieve {expected_source}; got "
        f"{[h['source'] for h in hits]}"
    )


@pytest.mark.slow
def test_ac11_scores_are_similarities_not_distances(index):
    """AC-11: a relevant hit must score higher than an irrelevant one."""
    relevant = index.search("warfarin fluconazole interaction", k=1)[0]
    unrelated = index.search("parking arrangements for visitors", k=1)[0]
    assert 0.0 <= relevant["score"] <= 1.0
    assert relevant["score"] > unrelated["score"]


@pytest.mark.slow
def test_ac11_index_is_reused_not_rebuilt(tmp_path):
    """AC-11: the index persists, so retrieval is not paying an embedding cost per run."""
    first = ClinicalGuidanceIndex(persist_dir=tmp_path / "chroma").build()
    second = ClinicalGuidanceIndex(persist_dir=tmp_path / "chroma").build()
    assert first["built"] is True
    assert second["reused"] is True
    assert second["fingerprint"] == first["fingerprint"]


# ---------------------------------------------------------------------------
# The tool is agentic, not scheduled
# ---------------------------------------------------------------------------


def test_ac11_retrieval_is_not_a_graph_node(tmp_tracer, tmp_path):
    """AC-11: retrieval is *not* a node in the graph — that is what makes it agentic.

    If it were a node, it would run on every case whether or not the case needed it, which is
    precisely the 'fixed step' the criterion excludes.
    """
    from discharge_copilot.graph import build_graph, make_checkpointer

    graph = build_graph(
        tmp_tracer, checkpointer=make_checkpointer(tmp_path / "cp.sqlite")
    )
    nodes = set(graph.get_graph().nodes)
    for forbidden in ("rag", "retrieve", "search", "search_clinical_guidance"):
        assert forbidden not in nodes


def test_ac11_tool_is_bound_to_the_workers_that_need_it():
    """AC-11: the tool is offered to the workers, which is how the model gets to choose."""
    from discharge_copilot.nodes.workers import WORKER_TOOLS

    for worker in ("summary", "medication", "followup", "education"):
        assert "search_clinical_guidance" in WORKER_TOOLS[worker]


def test_ac11_tool_description_guides_the_decision():
    """AC-11: the description must tell the model when NOT to call it.

    A tool whose description only says what it does gets called reflexively. The negative
    instruction is what makes the choice meaningful.
    """
    tool = make_rag_tool()
    assert tool.name == "search_clinical_guidance"
    description = tool.description.lower()
    assert "call this when" in description
    assert "do not call" in description


@pytest.mark.slow
def test_ac11_tool_invocation_is_traced_with_the_agents_own_query(tmp_tracer):
    """AC-11: the trace records what the agent chose to look up — the AC-11 evidence."""
    tool = make_rag_tool(tmp_tracer)
    output = tool.invoke(
        {"query": "warfarin interaction with fluconazole at discharge", "k": 2}
    )
    assert "Retrieved" in output
    assert "anticoagulation" in output.lower()

    events = [e for e in tmp_tracer.events if e["event"] == "rag_query"]
    assert events
    assert events[0]["decided_by"] == "agent"
    assert events[0]["hits"] > 0
    assert any(
        e["event"] == "tool_call" and e["tool"] == "search_clinical_guidance"
        for e in tmp_tracer.events
    )


def test_ac11_retrieval_failure_degrades_gracefully(tmp_tracer, monkeypatch):
    """AC-11/NFR-07: an unavailable index returns a readable note, not an exception."""
    from discharge_copilot.tools import rag

    def boom():
        raise RuntimeError("chroma is unavailable")

    monkeypatch.setattr(rag, "get_index", boom)
    result = make_rag_tool(tmp_tracer).invoke({"query": "anything", "k": 2})
    assert "RETRIEVAL ERROR" in result
    assert any(e["event"] == "rag_failed" for e in tmp_tracer.events)
