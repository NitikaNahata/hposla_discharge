# Memory Design — Tiers, Persistence and Eviction

**Covers:** AC-06 (tiered memory) · AC-07 (cross-session persistence, deterministically scored) ·
AC-08 (eviction policy)

---

## 1. Three tiers

| Tier | Store | Question it answers | Lifetime | Module |
| --- | --- | --- | --- | --- |
| **T1 working** | `DischargeState` + checkpoint | What has happened in this run? | the case | `memory/working.py` |
| **T2 episodic** | SQLite file | What do we know about this patient? | durable | `memory/episodic.py` |
| **T3 semantic** | Chroma + local embeddings | What do we know relevant to *this question*? | durable | `memory/semantic.py` |

`TieredMemory` (`memory/__init__.py`) is the only interface the graph sees. Nodes call
`recall_for_patient()` at intake and `write_case_facts()` at finalize; they never touch a tier
directly, so the tiering can change without touching node code.

### Why T2 and T3 both exist

They fail differently, and the failures are complementary.

**Episodic is exact and complete.** Ask for a patient's facts and you get all of them, ranked, with
no embedding model in the loop to go wrong. It cannot miss a fact it holds. But it can only answer
questions someone anticipated at write time.

**Semantic is fuzzy and query-shaped.** Asking *"why might this patient not attend her follow-up?"*
retrieves *"has previously missed cardiology appointments because she has no transport and does not
drive"* — a match with almost no shared vocabulary. No exact-key scheme connects those. But it can
miss things, and it degrades quietly.

Writes go to both. Recall merges them and de-duplicates on key: semantic hits lead because they are
query-relevant, then episodic fills in high-importance facts the query did not happen to match. An
allergy must surface whether or not anyone asked about allergies.

### Why not LangMem or the LangGraph Store

Both are in the approved stack and both were considered. The tiers here are a thin layer over
SQLite and Chroma directly, because AC-07 is **deterministically scored** and the test has to prove
persistence across a process boundary unambiguously. Owning the storage layer means the test can
point at a file and a subprocess, with no framework caching between the claim and the evidence.
For a production system with more memory types, LangMem's abstractions would earn their place.

---

## 2. Cross-session persistence (AC-07)

Both durable tiers are files on disk — `.state/episodic_memory.sqlite` and `.state/chroma_memory/`.
No external database service, per the No-Docker rule.

### How it is proven

`tests/test_ac07_cross_session_memory.py` runs session 2 in a **real subprocess** — a separate
Python interpreter with no shared objects, no warm caches, no inherited module state. Session 1
writes its facts and every handle is torn down before session 2 starts.

The subprocess boundary is the entire point. Two `TieredMemory` instances inside one interpreter
could appear to persist while actually sharing a page cache or a module-level singleton. A
subprocess cannot: if session 2 recalls the fact, the fact came off disk.

The semantic tier is tested the same way, and additionally with a query that shares no significant
vocabulary with the stored text — so the test cannot pass on a lucky substring match.

### Provenance

Every fact records the `session_id` that wrote it. A fact recalled in session 2 carries
`session_id="session-1"`, which is what makes cross-session recall *demonstrable* rather than
merely asserted. Tests assert on that field, not just on the content.

### Restating a fact does not reset its age

Writing an existing key updates content and recency but **preserves the original `created_at` and
`access_count`**. A penicillin allergy restated at a third admission is the same fact, not a new
one. Resetting its age would defeat recency decay entirely — a fact mentioned every admission would
never age, and the eviction policy would be inert.

**Evidence:** `evidence/logs/ac07_cross_session_persistence.log` (verbatim pytest output).

---

## 3. Eviction and importance (AC-08)

**Policy: importance-weighted, with a TTL floor and a per-namespace cap.**

```
effective_importance = base_importance × recency_decay(age) + access_boost(access_count)
```

Three mechanisms, because no single one is right on its own:

- **Importance weighting** decides what is worth keeping. "Allergic to penicillin" and "spouse
  drove him home" are not equally durable, and pure recency or LRU cannot tell them apart.
- **Recency decay** (exponential, 180-day half-life) stops a fact learned two years ago from
  outranking one learned last week, without deleting it.
- **Access boost** (saturating at +0.15) lets facts that keep proving useful earn their place —
  something pure importance scoring cannot express.

### Base importance by category

| Category | Base | Rationale |
| --- | --- | --- |
| `allergy` | 1.00 | Never safe to forget |
| `adverse_reaction` | 0.95 | |
| `high_alert_medication` | 0.85 | |
| `care_constraint` | 0.80 | Lives alone, no caregiver, no transport |
| `risk_tier` | 0.75 | |
| `adherence_concern` | 0.70 | |
| `caregiver` | 0.65 | |
| `medication_change` / `language_preference` | 0.60 | |
| `diagnosis` | 0.55 | |
| `followup_commitment` / `escalation` | 0.50 | |
| `admission_event` | 0.40 | |
| `observation` | 0.25 | Incidental ward colour |

Facts at or above **0.75** are **permanent** — exempt from TTL expiry entirely.

### Two-stage sweep

1. **TTL sweep.** Anything below the importance floor whose age exceeds the TTL is dropped.
   Permanent facts are skipped: *a penicillin allergy does not stop being true after 30 days.*
2. **Capacity sweep.** If the namespace still exceeds `max_per_namespace`, the lowest effective
   importance is dropped until it fits.

Every eviction records a machine-readable reason, so the log is auditable rather than opaque.
Eviction runs once per case at `finalize`, and mirrors into the semantic tier so the two do not
drift.

### The property that matters

> An allergy survives indefinitely. "Watched television in the day room" ages out.

A pure TTL or LRU policy could not make that distinction, and would eventually drop the allergy.
`tests/test_ac08_eviction_policy.py` pins both ends of this explicitly.

**Evidence:** `evidence/logs/ac08_eviction.log` — before/after tables with per-fact effective
importance and the reason for each eviction.

---

## 4. What gets written, and what does not

At `finalize`, these are extracted:

- every documented **allergy**
- **caregiver**, or explicitly the absence of one
- **care constraints** — lives alone, limited mobility, language
- the assessed **risk tier** with its contributing factors
- the **admission event**
- **medication changes** (start / stop / modify — never plain continues)
- **serious interactions** found
- **unreconciled medications** left needing a decision
- **follow-up commitments** and whether the enhanced pathway was applied
- **pharmacist escalations**

Deliberately *not* written: the full hospital course, the education packet text, routine
`continue` decisions, and anything already reconstructible from the packet.

The reason is that recall has a budget. `recall_for_patient` returns a handful of facts into a
worker's context; filling that budget with prose no one needed makes the mechanism worse than not
having it. Memory is for what a clinician would want surfaced **without going looking**.

---

## 5. Working memory (T1) and compression

Working memory is the message history plus the run's scratch, checkpointed with the state. It is
bounded: the most recent `DISCHARGE_WORKING_MEMORY_WINDOW` turns stay verbatim, and everything
older is handed to the compression middleware once the transcript passes the token threshold. See
[`context-engineering.md`](context-engineering.md) §3.

---

## 6. Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCHARGE_MEMORY_TTL_DAYS` | 30 | TTL for non-permanent facts |
| `DISCHARGE_MEMORY_MAX_PER_NAMESPACE` | 50 | Per-patient cap |
| `DISCHARGE_MEMORY_IMPORTANCE_FLOOR` | 0.25 | Effective importance below which a fact may expire |
| `DISCHARGE_WORKING_MEMORY_WINDOW` | 8 | Turns kept verbatim |
| `DISCHARGE_COMPRESSION_TRIGGER_TOKENS` | 3000 | Threshold at which compression fires |
| `DISCHARGE_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Local embeddings, no API calls |

Inspect what the system remembers about a patient:

```bash
python -m discharge_copilot memory --patient MRN-2001
```
