# Evidence Index

Every rubric parameter mapped to the committed artifact that evidences it, and every acceptance
criterion mapped to its test.

Per the **Evidence-in-Repo Rule**, uncommitted behaviour does not count. Everything referenced
here is a file in this repository, regenerable with:

```bash
./run.sh                              # everything
python scripts/generate_evidence.py   # evidence only
python scripts/generate_evidence.py --offline   # no API key needed
```

---

## 1. Rubric parameters → artifacts (22 parameters, 100 marks)

### Business & Requirements — 10 marks

| Parameter | Marks | Artifact |
| --- | --- | --- |
| Business-case clarity | 4 | [`business-case.md`](business-case.md) — problem, actors, workflow, success metrics |
| AC definition | 3 | [`acceptance-criteria.md`](acceptance-criteria.md) — AC-01…AC-12 + NFR-01…NFR-08, each mapped to a test and an artifact |
| Single-vs-multi justification | 3 | [`single-vs-multi-agent.md`](single-vs-multi-agent.md) + [`../evidence/logs/comparison.json`](../evidence/logs/comparison.json) — argued, then measured |

### Agent Architecture & LangGraph — 24 marks

| Parameter | Marks | Artifact |
| --- | --- | --- |
| Typed state | 5 | [`state.py`](../src/discharge_copilot/state.py) · [`architecture.md §2`](architecture.md) · `tests/test_ac01_typed_state.py` |
| Graph topology | 6 | [`graph.py`](../src/discharge_copilot/graph.py) · mermaid diagram in [`architecture.md §1`](architecture.md) · `tests/test_ac02_supervisor_routing.py` |
| Conditional routing | 6 | Four `route_*` functions in `graph.py` · `tests/test_ac03_conditional_routing.py` · `routing_decision` events in every trace |
| **Checkpointing** *(deterministic)* | 4 | `SqliteSaver` in `graph.py` · [`ac05_pause.md`](../evidence/transcripts/ac05_pause.md) · [`ac05_resume.md`](../evidence/transcripts/ac05_resume.md) · `tests/test_ac05_checkpoint_resume.py` |
| Structured output | 3 | [`schemas.py`](../src/discharge_copilot/schemas.py) · `structured_output` events in traces · `tests/test_ac04_structured_output.py` |

### Patterns & Multi-Agent — 18 marks

| Parameter | Marks | Artifact |
| --- | --- | --- |
| Pattern implementation | 6 | Plan-execute (`nodes/supervisor.py`), ReAct (`nodes/workers.py::_react_phase`), reflection (`nodes/reflection.py`); rationale in [`architecture.md §4`](architecture.md) |
| Multi-agent orchestration + transcript | 7 | [`supervisor_run_case_001.md`](../evidence/transcripts/supervisor_run_case_001.md) and one per sample case |
| Reflection / self-healing | 5 | [`ac12_fault_injection.md`](../evidence/transcripts/ac12_fault_injection.md) · `reflection` events in traces · `tests/test_ac12_reflection_selfheal.py` |

### Context Engineering — 12 marks

| Parameter | Marks | Artifact |
| --- | --- | --- |
| Write/select/compress/isolate | 5 | [`context-engineering.md`](context-engineering.md) — each strategy mapped to code and evidence |
| Compression middleware | 4 | [`nodes/compress.py`](../src/discharge_copilot/nodes/compress.py) · [`nfr08_compression.log`](../evidence/logs/nfr08_compression.log) |
| Context quarantine | 3 | [`context/quarantine.py`](../src/discharge_copilot/context/quarantine.py) · [`nfr03_injection_defence.log`](../evidence/logs/nfr03_injection_defence.log) · `data/samples/case_004.json` |

### Memory Systems — 14 marks

| Parameter | Marks | Artifact |
| --- | --- | --- |
| Tiered memory | 5 | [`memory/`](../src/discharge_copilot/memory/) — three tiers · [`ac06_tiered_memory.log`](../evidence/logs/ac06_tiered_memory.log) |
| **Cross-session persistence** *(deterministic)* | 6 | `tests/test_ac07_cross_session_memory.py` · [`ac07_cross_session_persistence.log`](../evidence/logs/ac07_cross_session_persistence.log) |
| Eviction policy | 3 | [`memory/policy.py`](../src/discharge_copilot/memory/policy.py) · [`memory-design.md §3`](memory-design.md) · [`ac08_eviction.log`](../evidence/logs/ac08_eviction.log) |

### MCP & Interoperability — 14 marks

| Parameter | Marks | Artifact |
| --- | --- | --- |
| **Custom MCP server** *(deterministic)* | 6 | [`mcp_server/discharge_server.py`](../mcp_server/discharge_server.py) — 4 tools, 2 resources · [`ac09_mcp_inventory.log`](../evidence/logs/ac09_mcp_inventory.log) |
| Adapter integration + tool-call log | 5 | [`tools/mcp_client.py`](../src/discharge_copilot/tools/mcp_client.py) · [`mcp_tool_calls.md`](../evidence/transcripts/mcp_tool_calls.md) · [`mcp_tool_calls.jsonl`](../evidence/transcripts/mcp_tool_calls.jsonl) |
| Integration decision | 3 | [`integration-decision.md`](integration-decision.md) — MCP vs API vs direct-DB vs A2A |

### Agentic RAG & Reproducibility — 8 marks

| Parameter | Marks | Artifact |
| --- | --- | --- |
| Agentic-RAG tool | 4 | [`tools/rag.py`](../src/discharge_copilot/tools/rag.py) · [`ac11_rag_decisions.log`](../evidence/logs/ac11_rag_decisions.log) — invoked vs declined |
| Reproducibility / secrets | 4 | [`README.md`](../README.md) · [`run.sh`](../run.sh) · [`.env.example`](../.env.example) · `.gitignore` · `tests/test_nfr_compliance.py::test_nfr01_*` |

---

## 2. Acceptance criteria → test → artifact

| AC | Test | Evidence |
| --- | --- | --- |
| AC-01 typed state | `test_ac01_typed_state.py` | `evidence/traces/case_001.jsonl` |
| AC-02 supervisor + workers | `test_ac02_supervisor_routing.py` | `evidence/transcripts/supervisor_run_case_001.md` |
| AC-03 conditional edges | `test_ac03_conditional_routing.py` | `case_002.jsonl` (risk branch) · `case_003.jsonl` (pharmacist branch) |
| AC-04 structured output | `test_ac04_structured_output.py` | `structured_output` events in all traces |
| AC-05 checkpoint pause/resume | `test_ac05_checkpoint_resume.py` | `ac05_pause.md` · `ac05_resume.md` · `ac05_checkpoint_resume.log` |
| AC-06 tiered memory | `test_ac06_tiered_memory.py` | `ac06_tiered_memory.log` |
| AC-07 cross-session persistence | `test_ac07_cross_session_memory.py` | `ac07_cross_session_persistence.log` |
| AC-08 eviction policy | `test_ac08_eviction_policy.py` | `ac08_eviction.log` |
| AC-09 MCP server | `test_ac09_ac10_mcp.py` | `ac09_mcp_inventory.log` |
| AC-10 adapter integration | `test_ac09_ac10_mcp.py` | `mcp_tool_calls.jsonl` · `mcp_tool_calls.md` |
| AC-11 agentic RAG | `test_ac11_agentic_rag.py` | `ac11_rag_decisions.log` |
| AC-12 reflection / self-healing | `test_ac12_reflection_selfheal.py` | `case_003_faultinject.jsonl` · `ac12_fault_injection.md` |

Every test docstring opens with its `AC-NN` identifier, so `grep -r "AC-07" tests/` resolves and
the AC-Traceability Rule is satisfiable by search.

| NFR | Test | Evidence |
| --- | --- | --- |
| NFR-01 no secrets | `test_nfr01_*` | `.env.example` · `.gitignore` |
| NFR-02 single command | `test_nfr02_*` | `run.sh` · `README.md` · `data/samples/` |
| NFR-03 quarantine | `test_nfr03_*` | `nfr03_injection_defence.log` |
| NFR-04 structured traces | `test_nfr04_*` | `evidence/traces/*.jsonl` |
| NFR-05 PII redaction | `test_nfr05_*` | pseudonymised identifiers throughout traces |
| NFR-06 decision documented | `test_nfr06_*` | `single-vs-multi-agent.md` · `comparison.json` |
| NFR-07 graceful degradation | `test_nfr07_*` | `ac12_fault_injection.md` |
| NFR-08 compression | `test_nfr08_*` | `nfr08_compression.log` |

---

## 3. Sample cases and what each exercises

| Case | Exercises |
| --- | --- |
| `case_001` | Baseline low-risk path. Clean reconciliation, no escalation. |
| `case_002` | **High readmission risk** → `route_risk_tier` → `enhanced_followup`. Also seeds an NSAID-in-heart-failure conflict. |
| `case_003` | **Contraindicated interaction** (warfarin + fluconazole) → `route_after_medication` → `pharmacist_review` and the AC-05 interrupt. Carries a deliberately unreconciled medication to trigger the structural critic check. |
| `case_004` | **Prompt injection** planted in a nurse handoff note (NFR-03), and a **session-2 readmission** of the `case_002` patient so cross-session memory is exercised in a real run. |

The four cases were chosen so that every conditional branch is reachable from committed input. A
branch no committed input can reach is untested in practice, whatever the unit tests say.

---

## 4. Reproducing

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add GOOGLE_API_KEY
./run.sh
```

Offline (no API key) still regenerates the MCP, memory, eviction and quarantine evidence, and
runs the full 200-test offline suite:

```bash
./run.sh --tests
python scripts/generate_evidence.py --offline
```
