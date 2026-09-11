# Production Readiness

**Status: production-*quality* code. Not production-*ready* for clinical use, and deliberately so.**

Those are different claims and conflating them in a healthcare system would be the most dangerous
thing in this repository. This document states plainly what has been engineered to a production
standard, what is intentionally a stub, and what would have to be true before anything here went
near a real patient.

The capstone brief settles part of this for us. §6.2 puts *"production deployment, multi-region,
authentication, and real back-end system integration"* out of scope, and the Open-Source &
No-Docker Rule requires the system to build and run with **pip and Python alone**. So the absence
of a Dockerfile, an auth layer or a deployment pipeline is not an oversight — building them would
have violated the brief.

---

## 1. What is engineered to a production standard

| Concern | How it is handled | Where |
| --- | --- | --- |
| **Input validation** | Every external input and every inter-agent handoff is a validated Pydantic model. A malformed worker artifact cannot enter graph state. | `schemas.py` |
| **Failure isolation** | Tool, model and memory failures degrade the run; they never kill it. A worker that fails loses one workstream, not the other three. | `llm.py`, `tools/`, `nodes/workers.py` |
| **Explicit exit conditions** | Every loop is bounded: supervisor steps, self-heal retries, ReAct tool iterations, LLM retries. No unbounded recursion. | `config.py`, `nodes/supervisor.py` |
| **Timeouts and retries** | Exponential backoff on transient model errors; retryable and non-retryable failures are distinguished rather than blanket-retried. | `llm.py` |
| **Durable state** | SQLite checkpointing keyed by `thread_id`; a crashed run resumes from its last checkpoint in a new process. | `graph.py` |
| **Secret hygiene** | Env-var configuration only, `.env` git-ignored, `.env.example` templated, a pre-commit hook and a CI job that both fail on a key-shaped string. | `.env.example`, CI, `.pre-commit-config.yaml` |
| **PII handling** | Identifiers are salted-hashed before they reach any log or trace. Per-process salt, so traces correlate internally without carrying identifiers. | `tracing.py` |
| **Untrusted input** | Clinical free-text is structurally quarantined, scanned for injection patterns, defanged against fence-breaking, and never treated as instruction. | `context/quarantine.py` |
| **Observability** | Structured JSONL traces of every node, routing decision, tool call, critic verdict and memory op, with token and cost accounting; cross-run aggregation. | `tracing.py`, `scripts/metrics.py` |
| **Test coverage** | 202 offline tests, one file per acceptance criterion, all passing with no API key. Cross-process claims proven across real subprocess boundaries. | `tests/` |
| **Static analysis** | `ruff` clean, `mypy` clean across 29 modules. Every suppression carries a written reason. | `pyproject.toml` |
| **CI** | Lint, types, tests on 3.11 and 3.12, plus a clean-clone install job that tests the README's own quick-start. | `.github/workflows/ci.yml` |
| **Reproducibility** | One documented command, committed sample inputs, pinned dependencies, deterministic synthetic backing data. | `run.sh` |

---

## 2. What is deliberately a stub

The brief permits stubs — *"heuristics and stubs are sufficient"* — and it matters that the
boundary is honest about which is which.

| Component | What it does | What production would do |
| --- | --- | --- |
| **MCP server backing data** | Reads synthetic JSON from `mcp_server/data/` | Call the real patient index, interaction service, and scheduling system. **The agent-side contract would not change** — that is the point of putting the boundary at MCP. |
| **`medication_interaction_check`** | 15 hand-written interaction pairs | A licensed interaction database (First Databank, Lexicomp) with full pairwise coverage and clinical review |
| **`schedule_followup`** | Deterministic synthetic clinic availability | Real scheduling system with locking, conflict resolution, and cancellation |
| **Readmission-risk model** | Transparent 9-factor weighted heuristic | A validated, calibrated model (LACE, HOSPITAL score) fitted and audited on real population data |
| **Discharge protocols** | 6 synthetic protocols | The institution's own reviewed, versioned clinical protocol library |
| **Patient data** | Four synthetic cases | Real records, which changes everything below |

---

## 3. What is genuinely missing for clinical production

This is the part a lint-clean repository can quietly hide. None of the following is a coding task.

### Regulatory and clinical
- **Clinical validation.** No prospective study, no measured effect on readmissions, no error-rate
  characterisation against clinician baseline. Every success metric in `business-case.md` is a
  target, not a finding.
- **Regulatory classification.** A tool influencing discharge decisions may constitute Clinical
  Decision Support software under FDA 21st Century Cures Act criteria, or a medical device under
  EU MDR. That determination has not been made, and it governs everything else.
- **Clinical governance.** No named clinical owner, no sign-off process, no incident pathway for
  when the copilot contributes to a patient-safety event.
- **Bias and equity audit.** The risk heuristic weights `language_barrier` and `no_caregiver` —
  socioeconomic proxies. On real data these could systematically route some groups to lesser
  follow-up. Unaudited, this is a fairness hazard, not a feature.

### Privacy and security
- **No PHI handling controls.** Synthetic data carries no obligations; real data brings HIPAA or
  GDPR, a BAA with the model provider, encryption at rest and in transit, key management, data
  residency, and retention limits. **None of that exists here.**
- **No authentication, authorisation, or audit trail of *users*.** Traces record what the agent
  did, never who asked. Out of scope per §6.2, and non-negotiable in production.
- **Prompt injection is mitigated, not solved.** `quarantine.py` is defence in depth: structural
  fencing plus pattern scanning. The structural control is load-bearing; the scanner is best-effort
  and a novel phrasing will evade it. It must not be treated as a guarantee.
- **Model provider dependency.** Clinical text is sent to a third-party API. In production that
  requires a BAA, a data-processing agreement, and very likely a different deployment posture.

### Operational
- **No SLOs, alerting, or on-call.** `scripts/metrics.py` aggregates committed traces after the
  fact; nothing watches a live system or pages anyone.
- **No rate limiting, quota management, or cost controls.** Token accounting exists; enforcement
  does not.
- **Single-process, single-node.** No concurrency beyond one case at a time, no queueing, no
  horizontal scale. SQLite is the right choice under the no-external-database rule and the wrong
  one for concurrent clinical load.
- **No rollback or model-version pinning discipline.** `DISCHARGE_MODEL` is configurable, but a
  provider-side model update could silently change clinical output with no canary or diff gate.

---

## 4. The honest summary

What this repository demonstrates is **agentic engineering**: a typed multi-agent graph with
conditional routing, durable checkpointing, validated handoffs, tiered memory that survives
process death, a versioned tool boundary, engineered context with a real injection defence, and a
self-healing loop evidenced by forced failures rather than described in prose.

What it is **not** is a system that should touch a patient. The gap is not code quality — that gap
has been closed. The gap is clinical validation, regulatory classification, privacy controls and
operational maturity, and those are measured in months of specialist work, not commits.

A discharge copilot that produced plausible output while quietly getting a medication
reconciliation wrong would be more dangerous than no copilot at all. That is why every artifact in
this system carries `requires_human_approval: true`, why major interactions interrupt the graph for
a pharmacist instead of resolving themselves, and why the critic's structural checks are
deterministic code rather than a model's opinion.

**This is a coordination aid with a human in the loop, by design and not by limitation.**
