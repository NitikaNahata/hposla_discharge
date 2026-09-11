"""Agentic-RAG tool: `search_clinical_guidance` (AC-11).

Retrieval here is **agentic**, not a pipeline stage. The tool is bound to the workers and the
model decides whether a given case needs a lookup at all. A straightforward pneumonia discharge
usually needs nothing; a warfarin patient starting fluconazole needs the interaction guidance
before it can reconcile safely. That contrast — a run where the agent calls it and a run where it
declines — is what `evidence/logs/ac11_rag_decisions.log` records, and it is what separates
agentic retrieval from a fixed step that always fires.

Why this is an in-process LangChain tool rather than an MCP tool: it searches the agent's *own*
embedded knowledge base, so it is part of the agent's cognition rather than an external hospital
system. The boundary rule is argued in `docs/integration-decision.md`.

Stack: Chroma (persistent, local) + Sentence-Transformers `all-MiniLM-L6-v2` (local embeddings,
no API calls). Both run from pip with no external service, per the No-Docker rule.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..config import get_config

COLLECTION = "clinical_guidance"

# A `---` front matter block splits into exactly three parts: before, block, after.
_FRONT_MATTER_PARTS = 3
# Sections shorter than this are headings or stubs, not retrievable content.
MIN_CHUNK_CHARS = 60


# ---------------------------------------------------------------------------
# Corpus loading and chunking
# ---------------------------------------------------------------------------


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split simple `---` front matter from a markdown document."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < _FRONT_MATTER_PARTS:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, parts[2].strip()


def chunk_document(text: str, *, source: str, meta: dict[str, str]) -> list[dict[str, Any]]:
    """Split a document on markdown section headings.

    Section-level chunking suits this corpus: each `##` heading is already a self-contained
    topic ("Follow-up intervals", "Red-flag symptoms"), so headings carry real semantic
    boundaries. Fixed-width chunking would cut through them.
    """
    chunks: list[dict[str, Any]] = []
    sections = re.split(r"\n(?=##\s)", text)
    for section in sections:
        body = section.strip()
        if len(body) < MIN_CHUNK_CHARS:
            continue
        heading_match = re.match(r"^#{1,3}\s+(.+)", body)
        heading = heading_match.group(1).strip() if heading_match else meta.get("title", source)
        chunks.append(
            {
                "text": body,
                "metadata": {
                    "source": source,
                    "heading": heading,
                    "title": meta.get("title", source),
                    "doc_type": meta.get("doc_type", "guidance"),
                    "conditions": meta.get("conditions", ""),
                    "medications": meta.get("medications", ""),
                },
            }
        )
    return chunks


def load_corpus(knowledge_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load and chunk every markdown document in the knowledge directory."""
    directory = knowledge_dir or get_config().knowledge_dir
    chunks: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.md")):
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        chunks.extend(chunk_document(body, source=path.name, meta=meta))
    return chunks


def corpus_fingerprint(chunks: list[dict[str, Any]]) -> str:
    """Content hash of the corpus, so the index rebuilds when the knowledge base changes."""
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk["text"].encode("utf-8"))
    return digest.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Vector store
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _embedder():
    """Local sentence-transformer. Cached — loading the model is the expensive part."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(get_config().embedding_model)


class ClinicalGuidanceIndex:
    """Persistent Chroma index over the synthetic clinical knowledge base."""

    def __init__(self, *, persist_dir: Path | None = None, knowledge_dir: Path | None = None):
        cfg = get_config()
        self.persist_dir = persist_dir or cfg.chroma_dir
        self.knowledge_dir = knowledge_dir or cfg.knowledge_dir
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._collection: Any = None

    def _client(self):
        import chromadb
        from chromadb.config import Settings

        return chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )

    def build(self, *, force: bool = False) -> dict[str, Any]:
        """Build or reuse the index. Rebuilds automatically when the corpus changes."""
        chunks = load_corpus(self.knowledge_dir)
        fingerprint = corpus_fingerprint(chunks)
        client = self._client()

        try:
            collection = client.get_collection(COLLECTION)
            stale = collection.metadata.get("fingerprint") != fingerprint
            if force or stale:
                client.delete_collection(COLLECTION)
                raise ValueError("rebuild")
            self._collection = collection
            return {
                "built": False,
                "reused": True,
                "chunks": collection.count(),
                "fingerprint": fingerprint,
            }
        except Exception:
            collection = client.create_collection(
                COLLECTION,
                metadata={"fingerprint": fingerprint, "hnsw:space": "cosine"},
            )

        embeddings = _embedder().encode(
            [c["text"] for c in chunks], normalize_embeddings=True
        ).tolist()
        collection.add(
            ids=[f"chunk-{i:03d}" for i in range(len(chunks))],
            documents=[c["text"] for c in chunks],
            metadatas=[c["metadata"] for c in chunks],
            embeddings=embeddings,
        )
        self._collection = collection
        return {
            "built": True,
            "reused": False,
            "chunks": len(chunks),
            "fingerprint": fingerprint,
            "documents": sorted({c["metadata"]["source"] for c in chunks}),
        }

    def collection(self):
        if self._collection is None:
            self.build()
        return self._collection

    def search(self, query: str, k: int = 3) -> list[dict[str, Any]]:
        """Semantic search over the corpus."""
        collection = self.collection()
        embedding = _embedder().encode([query], normalize_embeddings=True).tolist()
        raw = collection.query(
            query_embeddings=embedding,
            n_results=min(k, max(collection.count(), 1)),
            include=["documents", "metadatas", "distances"],
        )
        results: list[dict[str, Any]] = []
        for doc, meta, distance in zip(
            raw["documents"][0], raw["metadatas"][0], raw["distances"][0], strict=False
        ):
            results.append(
                {
                    "text": doc,
                    "source": meta.get("source", ""),
                    "heading": meta.get("heading", ""),
                    "title": meta.get("title", ""),
                    "doc_type": meta.get("doc_type", ""),
                    # Chroma returns cosine distance; convert to a similarity score.
                    "score": round(1.0 - float(distance), 4),
                }
            )
        return results


@lru_cache(maxsize=1)
def get_index() -> ClinicalGuidanceIndex:
    """Process-wide index. Cached so the embedding model loads once."""
    index = ClinicalGuidanceIndex()
    index.build()
    return index


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


class GuidanceQuery(BaseModel):
    """Arguments for `search_clinical_guidance`."""

    query: str = Field(
        description=(
            "What you need to know, phrased as a clinical question. Be specific — "
            "'warfarin interaction with fluconazole at discharge' retrieves far better than "
            "'medications'."
        )
    )
    k: int = Field(
        default=3, ge=1, le=6, description="How many passages to retrieve."
    )


def make_rag_tool(tracer: Any = None) -> StructuredTool:
    """Build the agentic-RAG tool bound to the workers (AC-11)."""

    def search_clinical_guidance(query: str, k: int = 3) -> str:
        try:
            hits = get_index().search(query, k=k)
        except Exception as exc:
            message = (
                f"RETRIEVAL ERROR: the guidance index is unavailable "
                f"({type(exc).__name__}: {exc}). Proceed using the case data you already have "
                "and note that guidance could not be consulted."
            )
            if tracer:
                tracer.emit("rag_failed", query=query, error=str(exc)[:300])
            return message

        if tracer:
            tracer.emit(
                "rag_query",
                query=query,
                k=k,
                hits=len(hits),
                sources=[h["source"] for h in hits],
                top_score=hits[0]["score"] if hits else 0.0,
                decided_by="agent",
            )
            tracer.tool_call(
                "search_clinical_guidance",
                "local",
                {"query": query, "k": k},
                [h["heading"] for h in hits],
                ok=True,
            )

        if not hits:
            return f"No guidance found for '{query}'."

        blocks = [
            f"[{i}] {h['title']} — {h['heading']} (relevance {h['score']:.2f}, "
            f"source: {h['source']})\n{h['text']}"
            for i, h in enumerate(hits, 1)
        ]
        return (
            f"Retrieved {len(hits)} passage(s) from the clinical guidance base for "
            f"'{query}':\n\n" + "\n\n".join(blocks)
        )

    return StructuredTool(
        name="search_clinical_guidance",
        description=(
            "Search the hospital's clinical guidance base for discharge protocols and "
            "medication guidance. Call this when you need authoritative guidance you do not "
            "already have — a follow-up interval for a specific condition, how to manage a "
            "particular drug interaction at discharge, required pre-discharge checks, or "
            "patient-education standards. Do NOT call it for information already present in "
            "the case: it retrieves reference material, not patient data."
        ),
        args_schema=GuidanceQuery,
        func=search_clinical_guidance,
    )
