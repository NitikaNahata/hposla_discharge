# Discharge Planning & Follow-up Copilot

**Multi-agent discharge coordination on LangGraph, with a custom MCP tool server, engineered
context, and tiered memory that survives across sessions.**

Business Case `AAIE_AGT_010_HLC` · Agentic AI Engineer Capstone

> A coordination aid, not medical advice. All data in this repository is synthetic. No real patient
> data, no PHI, no confidential data.

---

## Quick start

```bash
# 1. Python 3.12+ and a virtual environment
python3.12 -m venv .venv && source .venv/bin/activate

# 2. Dependencies (pip only — no Docker, no external database service)
pip install -r requirements.txt

# 3. Configure your Gemini API key
cp .env.example .env
#    then edit .env and set GOOGLE_API_KEY=...

# 4. Run everything: all sample cases, all tests, full evidence regeneration
./run.sh
```

`./run.sh` is the single documented command (NFR-02). It builds the RAG index, runs all four
committed sample discharges end to end, executes the AC test suite, and regenerates every artifact
under `evidence/`.

### Running one case

```bash
python -m discharge_copilot run --case data/samples/case_001.json
```

### Other entry points

```bash
# Pause at the pharmacist-review interrupt, then resume in a SEPARATE process (AC-05)
python -m discharge_copilot run --case data/samples/case_003.json --fresh --pause-after medication
python -m discharge_copilot resume --case-id CASE-003

# Force a real MCP timeout to exercise the self-healing loop (AC-12)
python -m discharge_copilot run --case data/samples/case_003.json --fresh --fault-inject mcp_timeout

python -m discharge_copilot memory --patient MRN-2001          # inspect tiered memory
python -m discharge_copilot show --case-id CASE-003            # inspect a checkpoint

pytest -v                                                      # AC traceability suite
streamlit run app/streamlit_app.py                             # routing + memory UI
python scripts/compare_single_vs_multi.py                      # measured NFR-06 comparison
```

> **`--fresh` matters.** Checkpoints are keyed by `thread_id = case_id`, so re-running a case that
> already completed *resumes* it and finalizes immediately — correct resume behaviour, and not what
> you want when re-running. `--fresh` discards the checkpoint first.

---

## What this system does

For a pending discharge, a supervisor agent plans and dispatches work to four specialist workers,
each producing one validated artifact:

| Worker | Produces |
| --- | --- |
| **Summary** | `DischargeSummary` — admission narrative, diagnoses, course, condition at discharge |
| **Medication reconciliation** | `MedicationReconciliation` — pre-admission vs. discharge medications, changes, interactions |
| **Follow-up scheduling** | `FollowUpPlan` — booked appointments with specialty, urgency and rationale |
| **Education** | `EducationPacket` — plain-language instructions, red-flag symptoms, caregiver guidance |

A critic grades every worker output before it is accepted. Low-confidence output is re-run with the
critic's issues attached; tool failures degrade to local fallbacks; major medication interactions
interrupt the graph for pharmacist review; high readmission risk routes to an enhanced follow-up
track.

### Graph topology

```
   case JSON
       │
       ▼
   ┌────────┐   quarantine untrusted free-text · score readmission risk
   │ intake │   · recall cross-session memory
   └───┬────┘
       ▼
  ┌────────────┐◀───────────────────────────────┐
  │ supervisor │  plan-execute: emits an ordered │
  └─────┬──────┘  plan, dispatches one worker    │
        │                                        │
  route_from_supervisor                          │
   ┌────┬──────────┬──────────┬────────┐         │
   ▼    ▼          ▼          ▼        ▼         │
summary medication followup education finalize   │
        │              │                         │
   route_after_    route_risk_tier               │
   medication          │                         │
        │              └──▶ enhanced_followup    │
        └──▶ pharmacist_review (interrupt)       │
                       │                         │
                       ▼                         │
                  ┌─────────┐                    │
                  │ reflect │──route_after_reflection
                  └─────────┘   accept  → supervisor
                                revise  → back to worker (self-heal)
                                escalate→ fallback
```

Four conditional routers, each a pure function of typed state and unit-tested at both branches
without invoking the LLM.

---

## Architecture at a glance

| Layer | Implementation |
| --- | --- |
| **Agent framework** | LangGraph 1.2 — `StateGraph` over the `DischargeState` TypedDict |
| **LLM** | Google Gemini (`gemini-3.6-flash`) via `langchain-google-genai` |
| **State** | `src/discharge_copilot/state.py` — typed, with per-field reducers |
| **Structured output** | Pydantic models in `schemas.py`, bound at every worker handoff |
| **Checkpointing** | `SqliteSaver` on `.state/checkpoints.sqlite`, keyed by `thread_id` |
| **Interoperability** | Custom MCP server (4 tools, 2 resources) over stdio via `langchain-mcp-adapters` |
| **Memory** | T1 working (in-state, windowed) · T2 episodic (SQLite) · T3 semantic (Chroma + `all-MiniLM-L6-v2`) |
| **Eviction** | Importance-weighted with TTL and per-namespace LRU cap |
| **Agentic RAG** | `search_clinical_guidance` — a bound tool the model elects to call, not a fixed stage |
| **Interface** | Typer CLI (primary) + Streamlit (routing and memory visualisation) |

---

## Repository map

```
docs/            business case, acceptance criteria, architecture, and the
                 decision records (single-vs-multi, integration, context, memory)
src/discharge_copilot/
  state.py       typed graph state (AC-01)
  schemas.py     Pydantic handoff contracts (AC-04)
  graph.py       topology, four conditional routers, checkpointer (AC-02/03/05)
  graph_single.py  single-agent variant, for the NFR-06 comparison
  nodes/         supervisor, four workers, reflection, compression, finalize
  memory/        working · episodic · semantic · eviction policy (AC-06/07/08)
  context/       quarantine and context assembly (NFR-03, NFR-08)
  tools/         agentic RAG + MCP adapter client (AC-10/11)
  tracing.py     structured JSONL traces with PII redaction (NFR-04/05)
mcp_server/      custom MCP server + synthetic backing data (AC-09)
knowledge/       synthetic protocols and medication guidance for the RAG corpus
data/samples/    four committed synthetic discharge cases
tests/           one test file per AC, each docstring carrying its AC-NN
evidence/        committed transcripts, traces and logs — the scored artifacts
scripts/         evidence regeneration and the single-vs-multi comparison
```

---

## Evidence

Per the Evidence-in-Repo Rule, uncommitted behavior does not count. Everything under `evidence/` is
committed and regenerable with `./run.sh`:

| Artifact | Demonstrates |
| --- | --- |
| `evidence/transcripts/supervisor_run_case_001.md` | Multi-agent orchestration end to end |
| `evidence/transcripts/mcp_tool_calls.jsonl` | The agent invoking MCP tools through the adapter |
| `evidence/transcripts/ac05_pause.md` / `ac05_resume.md` | Pause and resume across two OS processes |
| `evidence/logs/ac07_cross_session_persistence.log` | Memory recalled by a fresh subprocess |
| `evidence/traces/case_003_selfheal.jsonl` | The critic catching and repairing a weak output |
| `evidence/traces/case_003_faultinject.jsonl` | A forced MCP timeout, degraded gracefully |
| `evidence/logs/nfr03_injection_defence.log` | A prompt injection in a nurse note, neutralised |
| `evidence/logs/comparison.json` | Single vs. multi-agent, measured |

Full mapping of every acceptance criterion to its test and artifact:
[`docs/acceptance-criteria.md`](docs/acceptance-criteria.md) and
[`docs/evidence-index.md`](docs/evidence-index.md).

---

## Documentation

| Document | Contents |
| --- | --- |
| [`docs/business-case.md`](docs/business-case.md) | Problem, actors, workflow, success metrics |
| [`docs/acceptance-criteria.md`](docs/acceptance-criteria.md) | AC-01…AC-12 and NFR-01…NFR-08 with traceability |
| [`docs/architecture.md`](docs/architecture.md) | State design, node contracts, agent patterns |
| [`docs/single-vs-multi-agent.md`](docs/single-vs-multi-agent.md) | Orchestration decision + measured comparison |
| [`docs/integration-decision.md`](docs/integration-decision.md) | MCP vs. API vs. direct-DB vs. A2A |
| [`docs/context-engineering.md`](docs/context-engineering.md) | Write / select / compress / isolate |
| [`docs/memory-design.md`](docs/memory-design.md) | Three tiers and the eviction policy |
| [`docs/evidence-index.md`](docs/evidence-index.md) | Every rubric parameter → its artifact |
| [`docs/production-readiness.md`](docs/production-readiness.md) | What is production-quality, what is a stub, and what clinical use would still require |

---

## Development

```bash
ruff check .          # lint (clean)
ruff format .         # format
mypy                  # type check (clean across 29 modules)
pytest -m "not live"  # 202 offline tests, no API key needed
pre-commit install    # optional: run lint/format/secret checks on commit
```

CI (`.github/workflows/ci.yml`) runs lint, types and the offline suite on Python 3.11 and 3.12,
plus a clean-clone job that installs exactly as this README instructs and fails if a key-shaped
string is ever committed.

## Notes

**Secrets.** No key is committed. Configuration is entirely by environment variable, templated in
[`.env.example`](.env.example); `.env` is git-ignored. `tests/test_nfr01_no_secrets.py` scans the
tree for key-shaped strings.

**MCP SDK version.** `requirements.txt` pins `mcp>=1.9,<2` deliberately. SDK 2.x renames `FastMCP` to
`MCPServer` and relocates `RequestContext`, which breaks `langchain-mcp-adapters` 0.3.x at import.
The mandated adapter constrains the SDK version; the pin is intentional, not stale.

**First run.** `sentence-transformers` downloads the `all-MiniLM-L6-v2` model (~90 MB) once, and the
Chroma index is built on first use. Subsequent runs are offline for retrieval.

**Python version.** 3.12 is recommended. 3.11+ is required by the stack; 3.14 is not yet reliable for
`torch`/`sentence-transformers` wheels.
