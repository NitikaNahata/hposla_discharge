# Single-Agent vs. Multi-Agent — Decision & Framework Rationale

**Decision:** a **supervisor-orchestrated multi-agent** system on **LangGraph**.
**Status:** accepted · **Applies to:** NFR-06, AC-02 · **Evidence:** `evidence/logs/comparison.json`

This document argues the decision, then tests it. Both variants are implemented and run against the
same cases, because a rationale that was never checked against a measurement is just a preference.

- Supervisor variant: `src/discharge_copilot/graph.py`
- Single-agent variant: `src/discharge_copilot/graph_single.py`
- Comparison harness: `scripts/compare_single_vs_multi.py`

---

## 1. The choice

A single ReAct agent holding every tool could produce a discharge packet. For a workload this size
that is a legitimate design, and it is the one we would pick if the output were a single artifact.
It is not. A discharge packet is **four distinct artifacts** with different schemas, different source
material, different failure modes, and different escalation targets.

Four properties of the problem pushed us to a supervisor topology:

**1. The outputs have genuinely different contracts.** `MedicationReconciliation` and
`EducationPacket` share almost no structure. A single agent must hold all four output schemas in
context simultaneously and decide which it is producing at each step. Separate workers each hold one
schema, and each binds `with_structured_output` to exactly that model — validity becomes structural
rather than a matter of the model remembering.

**2. Context requirements differ sharply per workstream.** Medication reconciliation needs the
formulary, the interaction checker, and the pre-admission medication list — and must *not* be
distracted by education reading-level guidance. Isolating context per worker is one of the four
context-engineering strategies (see [`context-engineering.md`](context-engineering.md)), and worker
boundaries are the natural place to enforce it. A single agent's context is necessarily the union of
all four needs, which is both larger and noisier.

**3. Escalation targets differ.** A major medication interaction goes to a pharmacist. A high
readmission risk goes to an enhanced follow-up track. These are different conditional edges out of
different nodes. In a single-agent design both become "the model decides to mention it" — an
observation in prose rather than a routing decision in the graph. With separate nodes,
`route_after_medication` and `route_risk_tier` are pure functions of typed state, unit-testable
without an LLM.

**4. Failures should be isolated and retryable.** When the medication worker produces a
low-confidence output, we want to re-run *that worker* with the critic's issues — not re-run the
entire discharge. Per-worker retry with a bounded budget is only meaningful if workers are separable.

### Why not a swarm?

Swarm (peer agents handing off directly to each other) was considered and rejected. Discharge work
has a genuine dependency order — the medication list informs the education packet; the risk tier
informs the follow-up plan — and a central planner expresses that ordering far more legibly than
emergent handoffs. Swarm also makes the step budget harder to bound, and NFR-07 requires explicit
exit conditions. The supervisor gives us one place to enforce `MAX_SUPERVISOR_STEPS`.

### Honest counter-argument

Multi-agent costs real tokens: the supervisor's planning turns are pure overhead, and state is
re-serialized at every handoff. For a *strictly linear* workload with one output schema, the
single-agent variant would be the better engineering choice. Our claim is not that multi-agent is
generally superior — it is that this workload's branching and schema diversity pay for the overhead.
Section 3 tests that claim.

---

## 2. Framework rationale — why LangGraph

LangGraph is mandated by the business case, but it is also the right tool, and it is worth stating
why rather than treating the requirement as the whole answer.

| Requirement | What LangGraph gives us | The alternative's problem |
| --- | --- | --- |
| Typed state shared across nodes (AC-01) | `StateGraph` over a `TypedDict` with per-field reducers | CrewAI passes task outputs as loosely-typed context; there is no single typed state object to point at |
| Conditional routing on state (AC-03) | `add_conditional_edges` with plain-Python router functions | Crew task dependencies are largely declared up front; dynamic routing on discovered state is awkward |
| Pause / resume (AC-05) | `SqliteSaver` checkpointer keyed by `thread_id` — durable across processes | No first-class equivalent; would require bespoke serialization |
| Human-in-the-loop escalation | `interrupt_before` on the pharmacist-review node | Would need an out-of-band mechanism |
| Cycles for self-healing (AC-12) | Cycles are native — a critic edge back to a worker is an ordinary edge | Crew is oriented toward a forward task pipeline; loops are unnatural |

**On CrewAI:** the spec permits it for the optional comparison. We did not use it, because the more
informative comparison is *single-agent vs. multi-agent within one framework* — that isolates the
orchestration variable. Comparing LangGraph-multi against CrewAI-multi would confound topology with
framework and answer neither question cleanly.

**On the model provider:** Google Gemini is the only approved provider. `gemini-3.6-flash` is the
default for both workers and critic — the reasoning here is structured extraction and routing rather
than deep synthesis, and Flash's latency makes the multi-agent step count affordable. The critic
model is configurable separately (`DISCHARGE_CRITIC_MODEL`) so it can be raised to Pro without
touching the workers.

---

## 3. Measured comparison

Both variants run the same four sample cases through the same tools, memory, and RAG corpus. The
only difference is orchestration. Reproduce with:

```bash
python scripts/compare_single_vs_multi.py
```

<!-- BEGIN:COMPARISON_RESULTS -->
*Populated by `scripts/compare_single_vs_multi.py`; raw data in `evidence/logs/comparison.json`.
This block is rewritten by the script — do not hand-edit.*
<!-- END:COMPARISON_RESULTS -->

### What we measure and why

| Metric | Why it matters |
| --- | --- |
| **Schema validity rate** | Whether all four outputs validate against their Pydantic models on the first attempt. The central claim for multi-agent — one schema per worker — predicts an advantage here. |
| **Conflict detection recall** | Seeded medication conflicts actually surfaced. Tests whether the dedicated medication worker outperforms a generalist agent juggling four concerns. |
| **Escalation correctness** | Major/contraindicated conflicts routed to pharmacist review, moderate ones not. Tests whether routing-as-graph-edge beats routing-as-prose. |
| **Total tokens** | The honest cost of the supervisor's planning overhead. |
| **Wall-clock latency** | Whether extra steps make the workflow impractical. |
| **Step count** | Direct measure of orchestration overhead. |

### Interpretation

Written after the run, in [`../evidence/logs/comparison.json`](../evidence/logs/comparison.json) and
summarised in the block above. The decision is reported as it measures — including if the
single-agent variant wins on a dimension. A comparison that could only confirm the choice already
made would not be worth running.
