"""Terminal and escalation nodes: pharmacist review, enhanced follow-up, and finalize.

`pharmacist_review` is the human-in-the-loop node. The graph is compiled with
`interrupt_before=["pharmacist_review"]`, so reaching it halts execution and persists a
checkpoint (AC-05). A later process resumes the same `thread_id` and continues — that
pause/resume pair is the AC-05 evidence.

`enhanced_followup` is the high-readmission-risk path reached from `route_risk_tier` (AC-03).

`finalize` assembles the validated `DischargePacket` and writes durable facts out to long-term
memory (the WRITE strategy of context engineering, and the mechanism AC-07 depends on).
"""

from __future__ import annotations

from typing import Any

from ..context.quarantine import flag_summary
from ..schemas import DischargePacket
from ..state import DischargeState


def make_pharmacist_review_node(tracer: Any):
    """Human-in-the-loop escalation for major medication interactions (AC-03, AC-05)."""

    def pharmacist_review(state: DischargeState) -> dict[str, Any]:
        with tracer.node("pharmacist_review") as carrier:
            recon = state.get("medications")
            interactions = recon.interactions if recon else []
            serious = [i for i in interactions if i.severity.requires_pharmacist]

            detail = "; ".join(
                f"{i.drug_a} + {i.drug_b} ({i.severity.value})" for i in serious
            ) or "flagged by reconciliation"

            tracer.emit(
                "human_in_the_loop",
                node="pharmacist_review",
                interactions=len(interactions),
                serious=len(serious),
                detail=detail,
                note="Graph interrupted here; state checkpointed for pharmacist sign-off.",
            )
            carrier.update(serious_interactions=len(serious))
            return {
                "escalations": [
                    f"PHARMACIST REVIEW REQUIRED: {detail}. "
                    "Discharge medications must not be dispensed until signed off."
                ]
            }

    return pharmacist_review


def make_enhanced_followup_node(tracer: Any):
    """High-risk enhanced follow-up path (AC-03, Good-to-Have readmission-risk routing)."""

    def enhanced_followup(state: DischargeState) -> dict[str, Any]:
        with tracer.node("enhanced_followup") as carrier:
            risk = state.get("risk")
            plan = state.get("followup")

            additions: list[str] = []
            if plan is not None:
                plan.enhanced_pathway = True
                existing = {s.lower() for s in plan.home_services}
                for service in (
                    "48-72 hour post-discharge telephone check",
                    "home health nursing assessment within 5 days",
                    "pharmacist medication review call within 7 days",
                ):
                    if service.lower() not in existing:
                        plan.home_services.append(service)
                        additions.append(service)

            tracer.emit(
                "enhanced_pathway_applied",
                risk_tier=risk.tier.value if risk else "unknown",
                risk_score=risk.score if risk else None,
                services_added=additions,
            )
            carrier.update(services_added=len(additions))

            update: dict[str, Any] = {
                "escalations": [
                    f"Enhanced follow-up pathway applied "
                    f"(readmission risk {risk.tier.value if risk else 'unknown'}, "
                    f"score {risk.score:.2f})." if risk else
                    "Enhanced follow-up pathway applied."
                ]
            }
            if plan is not None:
                update["followup"] = plan
            return update

    return enhanced_followup


def make_finalize_node(tracer: Any, memory: Any = None):
    """Assemble the final packet and write durable facts to long-term memory."""

    def finalize(state: DischargeState) -> dict[str, Any]:
        with tracer.node("finalize") as carrier:
            patient = state["patient"]
            packet = DischargePacket(
                case_id=state["case_id"],
                patient_mrn=patient.mrn,
                risk=state.get("risk"),
                summary=state.get("summary"),
                medications=state.get("medications"),
                followup=state.get("followup"),
                education=state.get("education"),
                escalations=list(state.get("escalations", [])),
                quarantine_flags=flag_summary(state.get("quarantined_notes") or []),
                requires_human_approval=True,
            )

            completeness = packet.completeness()

            # --- WRITE: persist durable facts for future sessions (AC-06, AC-07) ---
            writes: list[dict[str, Any]] = []
            if memory is not None:
                writes = memory.write_case_facts(state, packet)
                tracer.memory_op(
                    "write",
                    tier="multi",
                    count=len(writes),
                    keys=[w.get("key", "") for w in writes],
                )

            tracer.emit(
                "packet_finalized",
                completeness=completeness,
                escalations=len(packet.escalations),
                quarantine_flags=len(packet.quarantine_flags),
                workstreams_completed=state.get("completed", []),
            )
            carrier.update(completeness=completeness)

            return {
                "packet": packet.model_dump(mode="json"),
                "status": "complete" if completeness == 1.0 else "partial",
                "memory_writes": writes,
            }

    return finalize
