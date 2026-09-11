"""AC-05 — A checkpointer persists graph state so a discharge case can be paused and resumed.

The load-bearing test here is `test_ac05_checkpoint_survives_a_separate_process`: it writes a
checkpoint, exits the interpreter entirely, and reads it back from a **new OS process**. Two
`SqliteSaver` instances inside one interpreter could share a page cache; two processes cannot.
That is the difference between demonstrating durability and demonstrating a warm cache.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from discharge_copilot.config import REPO_ROOT
from discharge_copilot.graph import build_graph, make_checkpointer


# `checkpoint_ns` is required when calling the saver directly; LangGraph injects it
# automatically during `invoke`.
def thread(case_id: str) -> dict:
    return {"configurable": {"thread_id": case_id, "checkpoint_ns": ""}}


THREAD = thread("AC05-TEST")


def test_ac05_graph_compiles_with_a_sqlite_checkpointer(tmp_tracer, tmp_path):
    """AC-05: the graph is compiled with a durable SQLite checkpointer."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    checkpointer = make_checkpointer(tmp_path / "cp.sqlite")
    assert isinstance(checkpointer, SqliteSaver)
    graph = build_graph(tmp_tracer, checkpointer=checkpointer)
    assert graph.checkpointer is checkpointer


def test_ac05_checkpointer_writes_a_database_file(tmp_path):
    """AC-05: checkpoints land on disk, not in memory."""
    db = tmp_path / "cp.sqlite"
    checkpointer = make_checkpointer(db)
    checkpointer.put(
        THREAD,
        {
            "v": 1, "id": "chk-1", "ts": "2026-09-09T00:00:00Z",
            "channel_values": {"case_id": "AC05-TEST", "completed": ["summary"]},
            "channel_versions": {}, "versions_seen": {},
        },
        {"source": "update", "step": 1, "parents": {}},
        {},
    )
    assert db.exists() and db.stat().st_size > 0


def test_ac05_graph_is_compiled_with_an_interrupt_for_human_review(tmp_tracer, tmp_path):
    """AC-05: the pharmacist-review node is an interrupt point, so the graph can pause."""
    graph = build_graph(
        tmp_tracer,
        checkpointer=make_checkpointer(tmp_path / "cp.sqlite"),
        with_interrupts=True,
    )
    assert "pharmacist_review" in (graph.interrupt_before_nodes or [])

    no_interrupt = build_graph(
        tmp_tracer,
        checkpointer=make_checkpointer(tmp_path / "cp2.sqlite"),
        with_interrupts=False,
    )
    assert not (no_interrupt.interrupt_before_nodes or [])


def test_ac05_checkpoint_survives_a_separate_process(tmp_path: Path):
    """AC-05: state written by one OS process is recovered by a different one.

    This is the criterion's actual claim — 'paused and resumed' means across a process
    boundary, not across two calls in one script.
    """
    db = tmp_path / "cross_process.sqlite"

    writer = textwrap.dedent(
        f"""
        import sys; sys.path.insert(0, {str(REPO_ROOT / "src")!r})
        from pathlib import Path
        from discharge_copilot.graph import make_checkpointer
        cp = make_checkpointer(Path({str(db)!r}))
        cp.put(
            {{"configurable": {{"thread_id": "AC05-TEST", "checkpoint_ns": ""}}}},
            {{"v": 1, "id": "chk-writer", "ts": "2026-09-09T00:00:00Z",
              "channel_values": {{"case_id": "AC05-TEST",
                                  "completed": ["summary", "medication"],
                                  "supervisor_steps": 3,
                                  "status": "paused_for_pharmacist"}},
              "channel_versions": {{}}, "versions_seen": {{}}}},
            {{"source": "update", "step": 3, "parents": {{}}}},
            {{}},
        )
        print("WROTE")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", writer], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert "WROTE" in result.stdout

    reader = textwrap.dedent(
        f"""
        import sys, json; sys.path.insert(0, {str(REPO_ROOT / "src")!r})
        from pathlib import Path
        from discharge_copilot.graph import make_checkpointer
        cp = make_checkpointer(Path({str(db)!r}))
        tup = cp.get_tuple({{"configurable": {{"thread_id": "AC05-TEST", "checkpoint_ns": ""}}}})
        print(json.dumps(tup.checkpoint["channel_values"]))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", reader], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr

    recovered = json.loads(result.stdout.strip().splitlines()[-1])
    assert recovered["case_id"] == "AC05-TEST"
    assert recovered["completed"] == ["summary", "medication"]
    assert recovered["supervisor_steps"] == 3
    assert recovered["status"] == "paused_for_pharmacist"


def test_ac05_checkpoints_are_isolated_by_thread_id(tmp_path):
    """AC-05: `thread_id` scopes a case, so concurrent discharges cannot read each other."""
    cp = make_checkpointer(tmp_path / "cp.sqlite")
    for case, completed in (("CASE-A", ["summary"]), ("CASE-B", ["medication"])):
        cp.put(
            thread(case),
            {"v": 1, "id": f"chk-{case}", "ts": "2026-09-09T00:00:00Z",
             "channel_values": {"case_id": case, "completed": completed},
             "channel_versions": {}, "versions_seen": {}},
            {"source": "update", "step": 1, "parents": {}},
            {},
        )
    a = cp.get_tuple(thread("CASE-A"))
    b = cp.get_tuple(thread("CASE-B"))
    assert a.checkpoint["channel_values"]["completed"] == ["summary"]
    assert b.checkpoint["channel_values"]["completed"] == ["medication"]
    assert cp.get_tuple(thread("CASE-MISSING")) is None


@pytest.mark.live
@pytest.mark.slow
def test_ac05_live_pause_and_resume_across_processes(tmp_path):
    """AC-05: a live run pauses at pharmacist review and a new process completes it."""
    env_case = REPO_ROOT / "data" / "samples" / "case_003.json"

    paused = subprocess.run(
        [sys.executable, "-m", "discharge_copilot", "run",
         "--case", str(env_case), "--pause-after", "medication", "--quiet"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=600,
    )
    assert paused.returncode == 0, paused.stderr
    assert "Paused" in paused.stdout

    resumed = subprocess.run(
        [sys.executable, "-m", "discharge_copilot", "resume",
         "--case-id", "CASE-003", "--quiet"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=600,
    )
    assert resumed.returncode == 0, resumed.stderr
    assert "Resumed from checkpoint" in resumed.stdout


def test_ac05_clear_thread_discards_a_completed_run(tmp_path):
    """AC-05: `--fresh` discards a checkpoint so a re-run is genuinely a re-run.

    Regression test. Durable checkpointing has a sharp edge: `thread_id = case_id`, so
    re-running a completed case *resumes* it and the supervisor finalizes immediately with
    every workstream already marked complete. Correct resume behaviour, and exactly wrong when
    the intent was to run the case again — it silently produced empty evidence.
    """
    from discharge_copilot.graph import clear_thread

    cp = make_checkpointer(tmp_path / "cp.sqlite")
    cp.put(
        thread("CASE-FRESH"),
        {"v": 1, "id": "chk-1", "ts": "2026-09-09T00:00:00Z",
         "channel_values": {"completed": ["summary", "medication", "followup", "education"],
                            "status": "complete"},
         "channel_versions": {}, "versions_seen": {}},
        {"source": "update", "step": 9, "parents": {}},
        {},
    )
    assert cp.get_tuple(thread("CASE-FRESH")) is not None

    assert clear_thread(cp, "CASE-FRESH") is True
    assert cp.get_tuple(thread("CASE-FRESH")) is None

    # Clearing an absent thread is a no-op, not an error.
    assert clear_thread(cp, "CASE-FRESH") is False


def test_ac05_clear_thread_is_scoped_to_one_case(tmp_path):
    """AC-05: `--fresh` on one case must not disturb another case's checkpoint."""
    from discharge_copilot.graph import clear_thread

    cp = make_checkpointer(tmp_path / "cp.sqlite")
    for case in ("CASE-A", "CASE-B"):
        cp.put(
            thread(case),
            {"v": 1, "id": f"chk-{case}", "ts": "2026-09-09T00:00:00Z",
             "channel_values": {"case_id": case}, "channel_versions": {}, "versions_seen": {}},
            {"source": "update", "step": 1, "parents": {}},
            {},
        )
    clear_thread(cp, "CASE-A")
    assert cp.get_tuple(thread("CASE-A")) is None
    assert cp.get_tuple(thread("CASE-B")) is not None
