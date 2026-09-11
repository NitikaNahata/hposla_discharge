"""Tier 2 — episodic memory over SQLite (AC-06, AC-07, AC-08).

Durable, structured, per-patient facts. Every write records the session that produced it, which
is what makes cross-session recall *demonstrable* rather than merely claimed: a fact retrieved in
session 2 carries the id of the session that wrote it.

SQLite on a file is the whole persistence mechanism — no external database service, per the
No-Docker rule. The connection is opened per operation rather than held, so nothing depends on a
process staying alive and the cross-process test in `tests/test_ac07_cross_session_memory.py`
exercises the same code path a real resume would.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import get_config
from .policy import EvictionPolicy, base_importance, effective_importance, policy_from_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodic_memory (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace     TEXT NOT NULL,          -- patient MRN
    key           TEXT NOT NULL,          -- stable fact identifier
    content       TEXT NOT NULL,          -- the fact, in plain language
    kind          TEXT NOT NULL,          -- category, drives base importance
    importance    REAL NOT NULL,
    session_id    TEXT NOT NULL,          -- session that wrote it
    case_id       TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(namespace, key)
);
CREATE INDEX IF NOT EXISTS idx_episodic_namespace ON episodic_memory(namespace);
CREATE INDEX IF NOT EXISTS idx_episodic_importance ON episodic_memory(namespace, importance DESC);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class EpisodicMemory:
    """Durable per-patient fact store."""

    def __init__(self, db_path: Path | None = None, policy: EvictionPolicy | None = None):
        self.db_path = db_path or get_config().episodic_db
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy = policy or policy_from_config()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # -- writes -------------------------------------------------------------

    def write(
        self,
        *,
        namespace: str,
        key: str,
        content: str,
        kind: str,
        session_id: str,
        case_id: str = "",
        importance: float | None = None,
    ) -> dict[str, Any]:
        """Write or update one fact.

        Re-writing an existing key updates the content and refreshes recency but **preserves
        the original `created_at` and `access_count`** — a fact restated in a later admission is
        the same fact, not a new one, and resetting its age would defeat recency decay.
        """
        score = base_importance(kind) if importance is None else float(importance)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO episodic_memory
                    (namespace, key, content, kind, importance, session_id, case_id,
                     created_at, last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    content       = excluded.content,
                    kind          = excluded.kind,
                    importance    = MAX(episodic_memory.importance, excluded.importance),
                    last_accessed = excluded.last_accessed,
                    case_id       = excluded.case_id
                """,
                (namespace, key, content, kind, score, session_id, case_id, now, now),
            )
        return {
            "namespace": namespace,
            "key": key,
            "content": content,
            "kind": kind,
            "importance": score,
            "session_id": session_id,
            "tier": "episodic",
        }

    # -- reads --------------------------------------------------------------

    def recall(
        self, namespace: str, *, limit: int = 10, touch: bool = True
    ) -> list[dict[str, Any]]:
        """Recall a patient's facts, highest effective importance first."""
        with self._connect() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM episodic_memory WHERE namespace = ?", (namespace,)
                )
            ]

        for row in rows:
            row["effective_importance"] = effective_importance(row)
        rows.sort(key=lambda r: r["effective_importance"], reverse=True)
        selected = rows[:limit]

        if touch and selected:
            self._touch(namespace, [r["key"] for r in selected])
        return selected

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        """Fetch one fact by key."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM episodic_memory WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        return dict(row) if row else None

    def _touch(self, namespace: str, keys: list[str]) -> None:
        """Record a recall, feeding the access boost in the eviction policy."""
        if not keys:
            return
        placeholders = ",".join("?" * len(keys))
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE episodic_memory
                   SET access_count = access_count + 1, last_accessed = ?
                 WHERE namespace = ? AND key IN ({placeholders})
                """,
                (_now(), namespace, *keys),
            )

    def namespaces(self) -> list[str]:
        with self._connect() as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT namespace FROM episodic_memory ORDER BY namespace"
                )
            ]

    def count(self, namespace: str | None = None) -> int:
        with self._connect() as conn:
            if namespace:
                return conn.execute(
                    "SELECT COUNT(*) FROM episodic_memory WHERE namespace = ?", (namespace,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM episodic_memory").fetchone()[0]

    def sessions_for(self, namespace: str) -> list[str]:
        """Distinct sessions that have written facts about this patient."""
        with self._connect() as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT session_id FROM episodic_memory WHERE namespace = ? "
                    "ORDER BY session_id",
                    (namespace,),
                )
            ]

    # -- eviction (AC-08) ----------------------------------------------------

    def evict(self, namespace: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Apply the eviction policy to one namespace."""
        with self._connect() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM episodic_memory WHERE namespace = ?", (namespace,)
                )
            ]

        plan = self.policy.plan_eviction(rows)
        if not dry_run and plan["evict"]:
            keys = [r["key"] for r in plan["evict"]]
            placeholders = ",".join("?" * len(keys))
            with self._connect() as conn:
                conn.execute(
                    f"DELETE FROM episodic_memory WHERE namespace = ? "
                    f"AND key IN ({placeholders})",
                    (namespace, *keys),
                )

        return {
            "namespace": namespace,
            "dry_run": dry_run,
            "evaluated": plan["evaluated"],
            "kept": plan["kept"],
            "evicted": plan["evicted"],
            "expired_by_ttl": plan["expired_by_ttl"],
            "evicted_by_capacity": plan["evicted_by_capacity"],
            "permanent_retained": plan["permanent_retained"],
            "evicted_keys": [r["key"] for r in plan["evict"]],
            "reasons": plan["reasons"],
        }

    def evict_all(self, *, dry_run: bool = False) -> list[dict[str, Any]]:
        return [self.evict(ns, dry_run=dry_run) for ns in self.namespaces()]

    def clear(self, namespace: str | None = None) -> None:
        """Test/reset helper."""
        with self._connect() as conn:
            if namespace:
                conn.execute("DELETE FROM episodic_memory WHERE namespace = ?", (namespace,))
            else:
                conn.execute("DELETE FROM episodic_memory")
