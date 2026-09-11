"""Tier 3 — semantic memory over Chroma (AC-06, AC-07).

Where episodic memory answers "what do we know about this patient", semantic memory answers
"what do we know that is *relevant to this question*". A follow-up worker asking about transport
should retrieve "has previously missed cardiology appointments because she has no transport"
without anyone having anticipated that phrasing at write time — which exact-key lookup cannot do.

Persistence is a Chroma directory on disk with local Sentence-Transformers embeddings, so recall
survives a process exit with no external service and no API calls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import get_config
from .policy import base_importance, effective_importance

COLLECTION = "patient_semantic_memory"


@lru_cache(maxsize=1)
def _embedder():
    """Local embedding model, cached — loading it is the expensive part."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(get_config().embedding_model)


class SemanticMemory:
    """Embedding-backed recall over durable patient facts."""

    def __init__(self, persist_dir: Path | None = None):
        cfg = get_config()
        # Kept in its own directory so the patient memory store and the RAG guidance
        # index cannot collide — they have different lifecycles and different privacy
        # properties, and sharing a directory would blur both.
        self.persist_dir = persist_dir or (cfg.state_dir / "chroma_memory")
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._collection: Any = None

    def _get_collection(self):
        if self._collection is None:
            import chromadb
            from chromadb.config import Settings

            client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=Settings(anonymized_telemetry=False, allow_reset=True),
            )
            self._collection = client.get_or_create_collection(
                COLLECTION, metadata={"hnsw:space": "cosine"}
            )
        return self._collection

    @staticmethod
    def _doc_id(namespace: str, key: str) -> str:
        return f"{namespace}::{key}"

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
        """Embed and store one fact. Upserts on (namespace, key)."""
        score = base_importance(kind) if importance is None else float(importance)
        embedding = _embedder().encode([content], normalize_embeddings=True).tolist()
        self._get_collection().upsert(
            ids=[self._doc_id(namespace, key)],
            documents=[content],
            embeddings=embedding,
            metadatas=[
                {
                    "namespace": namespace,
                    "key": key,
                    "kind": kind,
                    "importance": score,
                    "session_id": session_id,
                    "case_id": case_id,
                    "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "access_count": 0,
                }
            ],
        )
        return {
            "namespace": namespace,
            "key": key,
            "content": content,
            "kind": kind,
            "importance": score,
            "session_id": session_id,
            "tier": "semantic",
        }

    # -- reads --------------------------------------------------------------

    def recall(
        self, namespace: str, query: str, *, k: int = 5, min_score: float = 0.15
    ) -> list[dict[str, Any]]:
        """Semantic recall scoped to one patient.

        Results are ranked by similarity *weighted by effective importance*, so a highly
        relevant but trivial observation does not outrank a slightly less similar allergy.
        """
        collection = self._get_collection()
        if collection.count() == 0:
            return []

        embedding = _embedder().encode([query], normalize_embeddings=True).tolist()
        raw = collection.query(
            query_embeddings=embedding,
            n_results=min(k * 3, max(collection.count(), 1)),
            where={"namespace": namespace},
            include=["documents", "metadatas", "distances"],
        )
        if not raw["ids"] or not raw["ids"][0]:
            return []

        hits: list[dict[str, Any]] = []
        for doc, meta, distance in zip(
            raw["documents"][0], raw["metadatas"][0], raw["distances"][0], strict=False
        ):
            similarity = 1.0 - float(distance)
            if similarity < min_score:
                continue
            importance = effective_importance(dict(meta))
            hits.append(
                {
                    "tier": "semantic",
                    "key": meta.get("key", ""),
                    "content": doc,
                    "kind": meta.get("kind", ""),
                    "importance": importance,
                    "similarity": round(similarity, 4),
                    # Relevance dominates but importance breaks ties toward safety.
                    "score": round(similarity * (0.6 + 0.4 * importance), 4),
                    "session_id": meta.get("session_id", ""),
                    "case_id": meta.get("case_id", ""),
                }
            )

        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:k]

    def count(self, namespace: str | None = None) -> int:
        collection = self._get_collection()
        if namespace is None:
            return collection.count()
        got = collection.get(where={"namespace": namespace}, include=[])
        return len(got.get("ids", []))

    def delete(self, namespace: str, keys: list[str]) -> int:
        """Remove facts — used to mirror episodic eviction into the vector store (AC-08)."""
        if not keys:
            return 0
        self._get_collection().delete(ids=[self._doc_id(namespace, k) for k in keys])
        return len(keys)

    def clear(self, namespace: str | None = None) -> None:
        """Test/reset helper."""
        collection = self._get_collection()
        if namespace:
            got = collection.get(where={"namespace": namespace}, include=[])
            if got.get("ids"):
                collection.delete(ids=got["ids"])
        else:
            got = collection.get(include=[])
            if got.get("ids"):
                collection.delete(ids=got["ids"])
