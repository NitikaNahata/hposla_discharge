"""Tiered memory facade (AC-06, AC-07, AC-08).

Three tiers, each answering a different question:

| Tier | Store | Question it answers | Lifetime |
| --- | --- | --- | --- |
| T1 working  | `DischargeState` + checkpoint | what has happened in this run | the case |
| T2 episodic | SQLite file                  | what do we know about this patient | durable |
| T3 semantic | Chroma + local embeddings    | what is relevant to *this* question | durable |

`TieredMemory` is what the graph sees. Nodes call `recall_for_patient` at intake and
`write_case_facts` at finalize; they never touch a tier directly, so the tiering can change
without touching node code.

**Why T2 and T3 both exist.** They fail differently. Episodic recall is exact and complete — ask
for a patient's facts and you get them, ranked, with no embedding in the loop to go wrong.
Semantic recall is fuzzy and query-shaped — it surfaces "no transport to appointments" when the
follow-up worker asks about scheduling barriers, which no exact-key scheme would have connected.
Writes go to both; recall merges them and de-duplicates on key.

Cross-session persistence (AC-07) is a property of both durable tiers: they are files on disk, so
a fact written by session 1 is readable by a *different process* in session 2. That is what
`tests/test_ac07_cross_session_memory.py` proves across a real subprocess boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..schemas import DischargePacket
from ..state import DischargeState, MemoryHit
from . import working
from .episodic import EpisodicMemory
from .policy import EvictionPolicy, base_importance, effective_importance, policy_from_config
from .semantic import SemanticMemory

# Below this effective importance a recalled fact is dropped once k hits are already
# in hand: weak matches crowd out strong ones in a bounded context window.
WEAK_RECALL_IMPORTANCE = 0.4


class TieredMemory:
    """The memory interface the graph uses."""

    def __init__(
        self,
        *,
        episodic_path: Path | None = None,
        semantic_path: Path | None = None,
        policy: EvictionPolicy | None = None,
        tracer: Any = None,
        enable_semantic: bool = True,
    ) -> None:
        self.policy = policy or policy_from_config()
        self.episodic = EpisodicMemory(db_path=episodic_path, policy=self.policy)
        self.tracer = tracer
        self.enable_semantic = enable_semantic
        self._semantic: SemanticMemory | None = None
        self._semantic_path = semantic_path

    @property
    def semantic(self) -> SemanticMemory | None:
        """Lazily constructed — loading the embedding model costs seconds."""
        if not self.enable_semantic:
            return None
        if self._semantic is None:
            self._semantic = SemanticMemory(persist_dir=self._semantic_path)
        return self._semantic

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
    ) -> dict[str, Any]:
        """Write one fact to both durable tiers."""
        record = self.episodic.write(
            namespace=namespace,
            key=key,
            content=content,
            kind=kind,
            session_id=session_id,
            case_id=case_id,
        )
        if self.semantic is not None:
            try:
                self.semantic.write(
                    namespace=namespace,
                    key=key,
                    content=content,
                    kind=kind,
                    session_id=session_id,
                    case_id=case_id,
                )
                record["tier"] = "episodic+semantic"
            except Exception as exc:
                if self.tracer:
                    self.tracer.emit(
                        "memory_semantic_write_failed", key=key, error=str(exc)[:200]
                    )
        return record

    def write_case_facts(
        self, state: DischargeState, packet: DischargePacket  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        """Extract durable facts from a completed case and persist them (the WRITE strategy).

        The selection is deliberately narrow. Everything in the packet is *available* next
        admission by reading the packet; memory is for the handful of facts a clinician would
        want surfaced without going looking — allergies, care constraints, adherence problems,
        and what was changed last time.
        """
        patient = state["patient"]
        namespace = patient.mrn
        session_id = state.get("session_id", "unknown")
        case_id = state.get("case_id", "")
        facts: list[tuple[str, str, str]] = []  # (key, content, kind)

        for allergy in patient.allergies:
            if allergy.strip():
                facts.append(
                    (
                        f"allergy:{allergy.strip().lower()}",
                        f"Documented allergy: {allergy.strip()}.",
                        "allergy",
                    )
                )

        if patient.caregiver.strip():
            facts.append(
                ("caregiver", f"Caregiver: {patient.caregiver.strip()}.", "caregiver")
            )
        else:
            facts.append(
                (
                    "care_constraint:no_caregiver",
                    "No identified caregiver at home.",
                    "care_constraint",
                )
            )

        if patient.lives_alone:
            facts.append(
                ("care_constraint:lives_alone", "Patient lives alone.", "care_constraint")
            )
        if patient.mobility_limited:
            facts.append(
                (
                    "care_constraint:mobility",
                    "Mobility is limited; requires a walking aid and assistance with transport.",
                    "care_constraint",
                )
            )
        if patient.primary_language.strip().lower() not in {"", "english"}:
            facts.append(
                (
                    "language_preference",
                    f"Primary language is {patient.primary_language}; "
                    "patient education must be produced in that language.",
                    "language_preference",
                )
            )

        risk = state.get("risk")
        if risk is not None:
            facts.append(
                (
                    "risk_tier",
                    f"Readmission risk assessed as {risk.tier.value} (score {risk.score:.2f}) "
                    f"on case {case_id}. Contributing factors: "
                    f"{', '.join(risk.factors) or 'none recorded'}.",
                    "risk_tier",
                )
            )

        facts.append(
            (
                f"admission:{case_id}",
                f"Admitted for {patient.primary_diagnosis}"
                + (
                    f" (discharged {patient.discharge_date})."
                    if patient.discharge_date
                    else "."
                ),
                "admission_event",
            )
        )

        recon = state.get("medications")
        if recon is not None:
            for change in recon.changes:
                if change.action in {"stop", "start", "modify"}:
                    facts.append(
                        (
                            f"med_change:{case_id}:{change.medication.strip().lower()}",
                            f"{change.medication} was {change.action}ped on case {case_id}: "
                            f"{change.reason}"
                            if change.action == "stop"
                            else f"{change.medication} was {change.action}ed on case "
                            f"{case_id}: {change.reason}",
                            "medication_change",
                        )
                    )
            for interaction in recon.interactions:
                if interaction.severity.requires_pharmacist:
                    facts.append(
                        (
                            f"interaction:{interaction.drug_a.lower()}"
                            f"+{interaction.drug_b.lower()}",
                            f"{interaction.severity.value.upper()} interaction between "
                            f"{interaction.drug_a} and {interaction.drug_b}: "
                            f"{interaction.description}",
                            "high_alert_medication",
                        )
                    )
            for med in recon.unreconciled:
                facts.append(
                    (
                        f"unreconciled:{med.strip().lower()}",
                        f"{med} was left unreconciled on case {case_id} and needs a decision.",
                        "adherence_concern",
                    )
                )

        plan = state.get("followup")
        if plan is not None:
            for appointment in plan.appointments:
                facts.append(
                    (
                        f"followup:{case_id}:{appointment.specialty.strip().lower()}",
                        f"Follow-up committed on case {case_id}: {appointment.specialty} "
                        f"within {appointment.within_days} days — {appointment.reason}",
                        "followup_commitment",
                    )
                )
            if plan.enhanced_pathway:
                facts.append(
                    (
                        f"enhanced_pathway:{case_id}",
                        f"Enhanced high-risk follow-up pathway applied on case {case_id}.",
                        "risk_tier",
                    )
                )

        for escalation in state.get("escalations", []):
            if escalation.startswith("PHARMACIST REVIEW"):
                facts.append(
                    (
                        f"escalation:{case_id}:pharmacist",
                        f"Case {case_id} required pharmacist review: {escalation[:200]}",
                        "escalation",
                    )
                )

        written: list[dict[str, Any]] = []
        for key, content, kind in facts:
            written.append(
                self.write(
                    namespace=namespace,
                    key=key,
                    content=content,
                    kind=kind,
                    session_id=session_id,
                    case_id=case_id,
                )
            )

        # Apply the retention policy once per case, so the store cannot grow unbounded (AC-08).
        eviction = self.episodic.evict(namespace)
        if eviction["evicted"] and self.semantic is not None:
            self.semantic.delete(namespace, eviction["evicted_keys"])
        if self.tracer and eviction["evaluated"]:
            self.tracer.emit(
                "memory_eviction",
                namespace_hash=namespace,
                **{k: v for k, v in eviction.items() if k not in {"reasons", "namespace"}},
            )

        return written

    # -- reads --------------------------------------------------------------

    def recall_for_patient(
        self, mrn: str, query: str, *, k: int = 6
    ) -> list[MemoryHit]:
        """Merged recall across both durable tiers (AC-06, AC-07).

        Semantic hits lead because they are query-relevant; episodic fills in high-importance
        facts the query did not happen to match — an allergy must surface whether or not anyone
        asked about allergies.
        """
        hits: list[MemoryHit] = []
        seen: set[str] = set()

        if self.semantic is not None:
            try:
                for hit in self.semantic.recall(mrn, query, k=k):
                    if hit["key"] in seen:
                        continue
                    seen.add(hit["key"])
                    hits.append(
                        MemoryHit(
                            tier="semantic",
                            key=hit["key"],
                            content=hit["content"],
                            importance=hit["importance"],
                            score=hit["score"],
                            session_id=hit["session_id"],
                        )
                    )
            except Exception as exc:
                if self.tracer:
                    self.tracer.emit("memory_semantic_recall_failed", error=str(exc)[:200])

        for record in self.episodic.recall(mrn, limit=k):
            if record["key"] in seen:
                continue
            # Only pull in episodic facts that matter on their own merits.
            if record["effective_importance"] < WEAK_RECALL_IMPORTANCE and len(hits) >= k:
                continue
            seen.add(record["key"])
            hits.append(
                MemoryHit(
                    tier="episodic",
                    key=record["key"],
                    content=record["content"],
                    importance=record["effective_importance"],
                    score=record["effective_importance"],
                    session_id=record["session_id"],
                )
            )

        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[: k + 2]

    # -- inspection ----------------------------------------------------------

    def snapshot(self, mrn: str) -> dict[str, Any]:
        """Everything the system remembers about a patient — used by the CLI and the UI."""
        records = self.episodic.recall(mrn, limit=200, touch=False)
        return {
            "namespace": mrn,
            "episodic_count": self.episodic.count(mrn),
            "semantic_count": self.semantic.count(mrn) if self.semantic else 0,
            "sessions": self.episodic.sessions_for(mrn),
            "facts": [
                {
                    "key": r["key"],
                    "content": r["content"],
                    "kind": r["kind"],
                    "base_importance": r["importance"],
                    "effective_importance": r["effective_importance"],
                    "session_id": r["session_id"],
                    "case_id": r["case_id"],
                    "created_at": r["created_at"],
                    "access_count": r["access_count"],
                    "permanent": self.policy.is_permanent(r),
                }
                for r in records
            ],
        }


__all__ = [
    "EpisodicMemory",
    "EvictionPolicy",
    "SemanticMemory",
    "TieredMemory",
    "base_importance",
    "effective_importance",
    "policy_from_config",
    "working",
]
