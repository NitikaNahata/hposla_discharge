# Integration Decision — MCP vs. direct API vs. direct DB vs. A2A

**Decision:** expose the hospital-side capabilities (patient lookup, medication interaction
checking, follow-up scheduling, transport availability, discharge protocols, formulary) through a
**custom MCP server over stdio**, consumed by the agent via **langchain-mcp-adapters**.

**Status:** accepted · **Applies to:** AC-09, AC-10 · **Implementation:** `mcp_server/discharge_server.py`, `src/discharge_copilot/tools/mcp_client.py`

---

## 1. The decision in one paragraph

A discharge copilot has to reach four kinds of hospital capability that in reality live in four
different systems: a patient index, a drug-interaction service, a scheduling system, and a protocol
library. The question is what the *boundary* between the agent and those systems should look like.
We chose MCP because the boundary needs to be **tool-shaped and self-describing** — the agent must
be able to discover what is callable, with what arguments, at runtime, and the same server must be
reusable by other agents and by non-agent clients without rewriting the integration.

---

## 2. Options considered

### Option A — Direct database access

Let the agent read a SQLite/Postgres schema and issue queries.

- ✅ Lowest latency; no serialization layer.
- ❌ The agent inherits the schema as its interface. Any schema change silently breaks the agent.
- ❌ No natural place to enforce authorization or audit — the agent gets whatever the connection
  grants, which for a clinical system is unacceptable even in a prototype.
- ❌ Write operations (booking a follow-up) become raw INSERTs with no validation or idempotency.
- ❌ The spec forbids an external database service, so this would collapse into a local file anyway,
  losing the only advantage.

**Rejected.** The interface is at the wrong altitude. An agent should call *capabilities*, not
tables.

### Option B — Direct REST/HTTP API calls with hand-written tool wrappers

Wrap each hospital endpoint in a `@tool`-decorated Python function.

- ✅ Simple and familiar; total control over schemas.
- ✅ No extra process to run.
- ❌ Every wrapper is bespoke and lives inside *this* agent. A second agent re-implements all of it.
- ❌ Tool descriptions, argument schemas and error semantics drift from the underlying service with
  nothing to keep them honest.
- ❌ No runtime discovery — the tool list is frozen at import time.
- ❌ Resources (protocol documents, the formulary) have no natural representation; they get
  awkwardly modelled as tools that return blobs.

**Rejected as the primary boundary**, though this remains the right pattern for genuinely
agent-local capabilities — which is exactly why the agentic-RAG tool (`tools/rag.py`) is a plain
in-process LangChain tool and not an MCP tool. See §4.

### Option C — A2A (agent-to-agent protocol)

Model the pharmacy and scheduling systems as peer *agents* and negotiate with them.

- ✅ Genuinely useful when the counterpart has autonomy — its own goals, planning, and the right to
  refuse or counter-offer.
- ❌ None of our counterparts have autonomy. A formulary lookup is a function call, not a
  negotiation. Wrapping it in agent-to-agent messaging adds a planning loop, a conversation state
  machine, and nondeterminism to what is deterministically a database read.
- ❌ Substantially more surface area to build and evidence within a 15-hour budget, for no gain.

**Rejected.** A2A solves coordination between autonomous parties; our problem is capability access.
The internal supervisor↔worker relationship *is* our multi-agent surface, and it is handled by
LangGraph.

### Option D — Custom MCP server over stdio ✅ **chosen**

Expose capabilities as MCP tools and resources; the agent loads them through
`langchain-mcp-adapters`.

- ✅ **Self-describing.** The server publishes tool names, JSON-Schema arguments and descriptions.
  The agent discovers them at runtime; adding a fifth tool requires no agent-side change.
- ✅ **Correct tool/resource distinction.** MCP separates *tools* (actions with side effects —
  booking a follow-up) from *resources* (addressable read-only content —
  `discharge://protocol/heart-failure`). That distinction is real in this domain, and Option B
  cannot express it.
- ✅ **Process isolation.** The server runs as a separate process. A hung interaction check cannot
  take down the graph; it surfaces as a timeout the self-healing loop can act on (NFR-07) — which is
  precisely what the fault-injection evidence exercises.
- ✅ **Reusable across clients.** The same server works from Claude Desktop, another LangGraph agent,
  or a plain MCP client. The integration is written once.
- ✅ **Honest seam for the real thing.** In production the server body would call real hospital
  systems; the agent-side contract would not change at all. Our synthetic JSON backing store sits
  behind exactly the boundary a real implementation would.
- ✅ **stdio, not HTTP.** No port binding, no service to deploy — satisfies the No-Docker /
  pip-and-Python-alone rule.
- ⚠️ **Cost:** an extra process, subprocess lifecycle management, and async plumbing. Accepted.
- ⚠️ **Cost:** roughly 50–150 ms per call versus an in-process function. Irrelevant next to LLM
  latency.

---

## 3. Consequences

**Positive.** The capability surface is versionable and testable independently of the agent
(`tests/test_ac09_mcp_server.py` exercises the server with no LLM involved). Tool-call evidence is
easy to capture at the boundary, which is what `evidence/transcripts/mcp_tool_calls.jsonl` is. The
process boundary gives us a realistic failure mode to demonstrate graceful degradation.

**Negative.** The agent must manage a subprocess and an async session, so `tools/mcp_client.py`
carries retry, timeout and clean-shutdown logic that Option B would not have needed. Startup pays a
one-time server-spawn cost.

**Known constraint.** We pin `mcp>=1.9,<2`. MCP SDK 2.x renames `FastMCP` to `MCPServer` and moves
`mcp.shared.context.RequestContext`, which breaks `langchain-mcp-adapters` 0.3.x at import time.
Since the rubric mandates adapter-based integration, the adapter constrains the SDK version. This is
recorded in `requirements.txt` so the pin is not mistaken for staleness.

---

## 4. Where the boundary is deliberately *not* MCP

Not everything should cross a process boundary, and choosing MCP everywhere would be as thoughtless
as choosing it nowhere.

| Capability | Boundary | Why |
| --- | --- | --- |
| Patient lookup, interaction check, scheduling, transport | **MCP** | Models an external hospital system. Belongs behind a versioned, discoverable, reusable interface. |
| Discharge protocols, formulary | **MCP resource** | Addressable read-only reference content — exactly what MCP resources are for. |
| Agentic-RAG semantic search (`search_clinical_guidance`) | **In-process LangChain tool** | Retrieval over the agent's *own* embedded knowledge base. It is part of the agent's cognition, not an external system; shipping it over stdio would add latency and a serialization hop for no isolation benefit. |
| Memory read/write | **In-process** | Memory is agent-internal state. Exposing it as a tool would let the model corrupt its own substrate. |

The rule we applied: **MCP for capabilities the agent does not own; in-process tools for capabilities
that are part of the agent itself.**

---

## 5. Server inventory

Implemented in `mcp_server/discharge_server.py`, backed by synthetic JSON in `mcp_server/data/`.

### Tools

| Tool | Signature | Purpose |
| --- | --- | --- |
| `patient_lookup` | `(mrn: str)` | Retrieve a synthetic patient record: demographics, admission, problem list, pre-admission medications. |
| `medication_interaction_check` | `(medications: list[str])` | Return pairwise interactions with severity `minor` / `moderate` / `major` / `contraindicated`. Drives the pharmacist-review conditional edge. |
| `schedule_followup` | `(mrn, specialty, within_days, reason)` | Book a follow-up against synthetic clinic availability; returns a confirmed slot or the next feasible one. |
| `check_transport_availability` | `(mrn, transport_type, discharge_date)` | Check patient transport for mobility-limited discharges. |

### Resources

| Resource URI | Purpose |
| --- | --- |
| `discharge://protocol/{condition}` | Condition-specific discharge protocol (heart failure, COPD, pneumonia, post-surgical, diabetes). |
| `formulary://medications` | The synthetic hospital formulary: names, classes, common doses, and high-alert designations. |

All content is synthetic and generated for this project (Synthetic-Data Rule).
