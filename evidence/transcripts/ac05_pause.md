# AC-05 — Process 1: Pause at the Interrupt

**Criteria:** AC-05 (deterministically scored)  
**Generated:** 2026-09-13T05:18:03+00:00  
**Regenerate:** `python scripts/generate_evidence.py`

> All data is synthetic. Identifiers are pseudonymised in traces (NFR-05).

---

The graph is compiled with `interrupt_before=['pharmacist_review']`. On reaching a
major interaction it halts, checkpoints to SQLite under `thread_id=CASE-003`, and
**this process exits**.

```
$ python -m discharge_copilot run --case data/samples/case_003.json --pause-after medication

╭───────────────────────────── Discharge Copilot ──────────────────────────────╮
│ CASE-003 · Deep vein thrombosis of the left lower extremity with pulmonary   │
│ embolism                                                                     │
│ session session-1 · trace case_003_pause                                     │
│ will pause before pharmacist review                                          │
╰──────────────────────────────────────────────────────────────────────────────╯
Discarded the existing checkpoint for CASE-003; starting fresh.
╭────────────────────────── Paused for human review ───────────────────────────╮
│ Paused before: pharmacist_review                                             │
│ State checkpointed to /app/.state/checkpoints.sqlite under thread CASE-003.  │
│                                                                              │
│ Resume in a NEW process with:                                                │
│   python -m discharge_copilot resume --case-id CASE-003                      │
╰──────────────────────────────────────────────────────────────────────────────╯

```
