# Business Case — Discharge Planning & Follow-up Copilot

**Business Case ID:** AAIE_AGT_010_HLC
**Domain:** Healthcare — Discharge Planning
**Track:** Agentic AI Core + Context Engineering & Memory + MCP & Interoperability

---

## 1. Problem

Hospital discharge is a *coordination* problem long before it is a clinical one. Four workstreams
must converge on a single patient at a single moment:

1. the **discharge summary** (what happened during this admission),
2. **medication reconciliation** (what the patient took before vs. what they take now, and whether
   the combination is safe),
3. **follow-up scheduling** (who sees them next, how soon, and in which specialty), and
4. **patient education** (what the patient and their caregiver must actually do at home).

In practice these are produced by different people, at different times, from different systems. The
context leaks between them. A pre-admission medication is never formally stopped. A follow-up is
booked for a specialty that the summary never justified. Education material is handed over in a
language or reading level the caregiver cannot use. None of these are exotic failures; they are the
ordinary result of four handoffs with no shared state.

The cost is concentrated and measurable: avoidable 30-day readmissions, post-discharge adverse drug
events, and missed first follow-up appointments.

**What this project builds:** a stateful, multi-agent *copilot* that holds all four workstreams in
one shared, typed state object, carries context across a multi-turn and multi-session plan, and
produces a reviewable discharge packet for a human clinician to approve.

**What this project explicitly is not:** medical advice, clinical decision-making, or an automated
actor. It is a coordination aid. Every clinical judgement is surfaced for human sign-off, and every
high-severity medication conflict is routed to a pharmacist rather than resolved autonomously.

---

## 2. Actors

### Human actors

| Actor | Relationship to the system | What they need from it |
| --- | --- | --- |
| **Discharging nurse / hospitalist** | Primary user. Initiates a pending discharge and approves the final packet. | A complete, consistent draft packet in one pass, with anything uncertain visibly flagged rather than silently guessed. |
| **Clinical pharmacist** | Escalation target. Receives cases where medication reconciliation surfaces a major or contraindicated interaction. | To be interrupted *only* when it matters, with the specific conflicting pair and the source of the finding. |
| **Patient & caregiver** | Recipients of the education packet and follow-up plan. | Plain-language instructions, explicit red-flag symptoms, and confirmed appointment times. |
| **Care-coordination / scheduling desk** | Downstream consumer of the follow-up plan. | Structured appointment requests with specialty, urgency window, and rationale. |

### System actors

| Actor | Role |
| --- | --- |
| **Supervisor agent** | Plans the ordered sequence of work for a case and dispatches to workers. Owns no domain output of its own. |
| **Worker agents** (summary · medication · follow-up · education) | Each owns exactly one workstream and returns one validated structured object. |
| **Reflection critic** | Grades every worker output before it is accepted into state; triggers revision or escalation. |
| **MCP tool server** | The interoperability boundary. Exposes patient lookup, interaction checking, scheduling and transport as tools, plus discharge protocols and the formulary as resources. |
| **Memory subsystem** | Three tiers holding working context, per-case episodes, and durable cross-session semantic facts. |

---

## 3. Workflow

```
pending discharge case
        │
        ▼
   intake ──── quarantine untrusted free-text · score readmission risk · recall prior-session memory
        │
        ▼
  supervisor ── emits an ordered plan, dispatches one worker at a time
        │
        ├──▶ summary agent          → DischargeSummary
        ├──▶ medication agent       → MedicationReconciliation ──▶ [major conflict?] ──▶ pharmacist review
        ├──▶ follow-up agent        → FollowUpPlan ──▶ [high readmission risk?] ──▶ enhanced follow-up
        └──▶ education agent        → EducationPacket
                    │
                    ▼
               reflection ── accept · revise (self-heal) · escalate
                    │
                    ▼
              final discharge packet → human approval
```

---

## 4. Success metrics

These are the metrics the business would track. Because all data in this project is synthetic, the
targets are stated as system-behaviour proxies that the committed evidence actually demonstrates.

### Business outcome metrics (what the hospital would measure)

| Metric | Baseline assumption | Target |
| --- | --- | --- |
| 30-day avoidable readmission rate | 15% | −2 pp within two quarters |
| Discharge packets containing an unreconciled pre-admission medication | ~20% | < 5% |
| First follow-up appointment booked before the patient leaves the ward | ~60% | > 90% |
| Clinician time spent assembling a discharge packet | ~45 min | < 15 min |

### System behaviour metrics (what this repository evidences)

| Metric | How it is measured here | Target |
| --- | --- | --- |
| **Structural validity** | Every worker handoff validates against its Pydantic schema | 100% of committed runs |
| **Conflict detection recall** | Seeded medication conflicts in the sample cases that are surfaced | 100% (4/4 seeded) |
| **Escalation precision** | Major/contraindicated conflicts routed to pharmacist review; moderate ones not | No false escalations in committed runs |
| **Cross-session recall** | Facts written in session 1 retrieved by a *separate process* in session 2 | Proven by `tests/test_ac07_cross_session_memory.py` |
| **Injection resistance** | Instructions embedded in untrusted clinical free-text that alter agent behaviour | 0 — evidenced by `case_004` |
| **Graceful degradation** | Runs completing with a usable packet despite an injected tool failure | 100% — evidenced by the fault-injection trace |
| **Reproducibility** | Full system runs from a single documented command on a clean clone | `./run.sh` |

---

## 5. Scope boundaries

**In scope:** the LangGraph multi-agent graph, a custom MCP server, context engineering, tiered
memory, an agentic-RAG lookup tool, a reflection/self-healing loop, and a CLI plus Streamlit
interface.

**Out of scope:** real EHR or pharmacy connectivity, clinical order entry, production deployment,
authentication, and any real transaction execution. Heuristics and stubs are sufficient — the
project is judged on agentic engineering, not on the sophistication of the clinical logic.

**Data:** all patient records, medications, protocols and notes in this repository are synthetic and
generated for this project. There is no real patient data or PHI, and no Virtusa confidential data.
Synthetic identifiers are hashed before they are written to any log (NFR-05).

---

## 6. Why an agentic system rather than a script

A deterministic pipeline could assemble a discharge packet from templates. It could not do the three
things that actually make this problem hard:

1. **Decide what to look up.** Whether a case needs a protocol lookup, an interaction check, or
   neither depends on the case. Retrieval must happen *inside* the reasoning loop, not as a fixed
   stage (AC-11).
2. **Route on discovered state.** A medication conflict discovered mid-run must change the path
   through the workflow — to a pharmacist, or to an enhanced follow-up track (AC-03).
3. **Recover from its own bad output.** A summary that omits the discharge diagnosis should be
   caught and re-attempted, not shipped (AC-12).

The single-vs-multi-agent decision and framework rationale are argued separately in
[`single-vs-multi-agent.md`](single-vs-multi-agent.md), with measured evidence from a comparison run.
