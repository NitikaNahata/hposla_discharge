# AC-12 — Self-Healing After a Forced Tool Failure

**Criteria:** AC-12 (reflection / self-healing) · NFR-07 (graceful degradation)  
**Generated:** 2026-09-09T19:37:44+00:00  
**Regenerate:** `python scripts/generate_evidence.py`

> All data is synthetic. Identifiers are pseudonymised in traces (NFR-05).

---

`--fault-inject mcp_timeout` makes the MCP server stall past the client
timeout. This is a **real** failure in the real transport, not a simulated
one: the server process genuinely does not answer.

The run degrades to local context, records the failure, and still produces a
usable packet — which is what NFR-07 asks for.

## Run timeline


### `     366ms` → node `intake`

`     366ms` **RISK** tier **high** (score 0.64) — readmitted within the last 12 months, 5 or more discharge medications, three or more active problems, diagnosis with a high 30-day readmission rate

`    5176ms` **MEMORY_OP** `{"op": "recall", "tier": "multi", "count": 8, "keys": ["allergy:sulfa drugs", "interaction:warfarin+amiodarone", "interaction:amiodarone+warfarin", "risk_tier", "admission:CASE-003", "followup:CASE-003:anticoagulation clinic", "interaction:warfarin+fluconazole", "followup:CASE-003:cardiology"], "cross_session": false}`

`    5176ms` ← `intake` done in 4809.9ms — `{"risk_tier": "high", "notes_quarantined": 4, "notes_flagged": 0, "memories_recalled": 8}`


### `    5178ms` → node `supervisor`

`    8856ms` **STRUCTURED_OUTPUT** `{"node": "supervisor", "schema": "SupervisorPlan", "schema_valid": true, "error": ""}`

`    8856ms` **SUPERVISOR** dispatches to **`medication`**  
    _Given the high readmission risk and acute pulmonary embolism diagnosis, immediate medication reconciliation for anticoagulation management takes highest clinical priority and is required before followup and education can proceed._

`    8856ms` ← `supervisor` done in 3678.6ms — `{"next_agent": "medication", "step": 1}`

`    8856ms` **ROUTE** `route_from_supervisor` → **`medication`**  
    _supervisor selected 'medication'_


### `    8857ms` → node `medication`

`   41267ms` **TOOL** `medication_interaction_check` (mcp, FAILED)  
    args: `{"medications": ["Warfarin", "Amiodarone", "Fluconazole", "Tiotropium", "Atorvastatin"]}`  
    result: `TOOL TIMEOUT: 'medication_interaction_check' did not respond within 30s. The hospital system may be unavailable. Proceed using the case data you already have and record that this check could not be completed.`

`   41270ms` **TOOL_FAILURE** `{"tool": "medication_interaction_check", "source": "mcp", "error": "timeout", "timeout_seconds": 30.0, "recovery": "degraded_to_local_context"}`

`   53575ms` **REACT_COMPLETE** `{"worker": "medication", "iterations": 2, "reason": "model_stopped_calling_tools"}`

`   60971ms` **STRUCTURED_OUTPUT** `{"node": "medication", "schema": "MedicationReconciliation", "schema_valid": true, "error": ""}`

`   60972ms` ← `medication` done in 52114.6ms — `{"ok": true, "worker": "medication", "schema": "MedicationReconciliation"}`

`   60972ms` **ROUTE** `route_after_medication` → **`pharmacist_review`**  
    _highest interaction severity=major; pharmacist_required=True_


### `   60974ms` → node `pharmacist_review`

`   60974ms` **HUMAN_IN_THE_LOOP** `{"node": "pharmacist_review", "interactions": 2, "serious": 2, "detail": "Warfarin + Amiodarone (major); Warfarin + Fluconazole (major)", "note": "Graph interrupted here; state checkpointed for pharmacist sign-off."}`

`   60974ms` ← `pharmacist_review` done in 0.8ms — `{"serious_interactions": 2}`


### `   60976ms` → node `reflect`

`   68771ms` **STRUCTURED_OUTPUT** `{"node": "reflect", "schema": "Reflection", "schema_valid": true, "error": ""}`

`   68771ms` **CRITIC** `medication` confidence **0.95** → **ACCEPT**

`   68772ms` ← `reflect` done in 7795.4ms — `{"action": "accept", "confidence": 0.95}`

`   68772ms` **ROUTE** `route_after_reflection` → **`supervisor`**  
    _critic accepted or escalated; returning to supervisor_


### `   68773ms` → node `supervisor`

`   72964ms` **STRUCTURED_OUTPUT** `{"node": "supervisor", "schema": "SupervisorPlan", "schema_valid": true, "error": ""}`

`   72964ms` **SUPERVISOR** dispatches to **`followup`**  
    _Medication reconciliation is complete, unlocking the follow-up workstream. Given the high readmission risk for a pulmonary embolism case, establishing the follow-up care plan promptly is prioritized before patient education._

`   72965ms` ← `supervisor` done in 4191.8ms — `{"next_agent": "followup", "step": 2}`

`   72965ms` **ROUTE** `route_from_supervisor` → **`followup`**  
    _supervisor selected 'followup'_


### `   72966ms` → node `followup`

`   80127ms` **AGENTIC RAG** the agent chose to look up:  
    _"high risk DVT PE warfarin amiodarone fluconazole follow up interval discharge"_ → ['anticoagulation_guidance.md', 'anticoagulation_guidance.md', 'hf_discharge_protocol.md']

`   80127ms` **TOOL** `search_clinical_guidance` (local, ok)  
    args: `{"query": "high risk DVT PE warfarin amiodarone fluconazole follow up interval discharge", "k": 3}`  
    result: `['Warfarin interactions at discharge', 'When a new interacting medication is started', 'Follow-up intervals']`

`  112180ms` **TOOL** `schedule_followup` (mcp, FAILED)  
    args: `{"specialty": "Anticoagulation Clinic", "within_days": 3, "discharge_date": "2026-09-06", "mrn": "CASE-003", "reason": "Urgent INR check and warfarin dose adjustment due to major interactions with new`  
    result: `TOOL TIMEOUT: 'schedule_followup' did not respond within 30s. The hospital system may be unavailable. Proceed using the case data you already have and record that this check could not be completed.`

`  112181ms` **TOOL_FAILURE** `{"tool": "schedule_followup", "source": "mcp", "error": "timeout", "timeout_seconds": 30.0, "recovery": "degraded_to_local_context"}`

`  119143ms` **REACT_COMPLETE** `{"worker": "followup", "iterations": 3, "reason": "model_stopped_calling_tools"}`

`  124869ms` **STRUCTURED_OUTPUT** `{"node": "followup", "schema": "FollowUpPlan", "schema_valid": true, "error": ""}`

`  124870ms` ← `followup` done in 51904.4ms — `{"ok": true, "worker": "followup", "schema": "FollowUpPlan"}`

`  124871ms` **ROUTE** `route_risk_tier` → **`enhanced_followup`**  
    _readmission risk tier=high (score 0.64)_


### `  124873ms` → node `enhanced_followup`

`  124874ms` **ENHANCED_PATHWAY_APPLIED** `{"risk_tier": "high", "risk_score": 0.64, "services_added": ["48-72 hour post-discharge telephone check", "home health nursing assessment within 5 days", "pharmacist medication review call within 7 days"]}`

`  124874ms` ← `enhanced_followup` done in 0.9ms — `{"services_added": 3}`


### `  124874ms` → node `reflect`

`  129588ms` **STRUCTURED_OUTPUT** `{"node": "reflect", "schema": "Reflection", "schema_valid": true, "error": ""}`

`  129589ms` **CRITIC** `followup` confidence **0.95** → **ACCEPT**

`  129589ms` ← `reflect` done in 4715.0ms — `{"action": "accept", "confidence": 0.95}`

`  129590ms` **ROUTE** `route_after_reflection` → **`supervisor`**  
    _critic accepted or escalated; returning to supervisor_


### `  129591ms` → node `supervisor`

`  133258ms` **STRUCTURED_OUTPUT** `{"node": "supervisor", "schema": "SupervisorPlan", "schema_valid": true, "error": ""}`

`  133258ms` **SUPERVISOR** dispatches to **`summary`**  
    _Medication reconciliation and follow-up scheduling are both complete, satisfying the hard dependency requirements for education. Selecting 'summary' next to document the clinical hospital course and discharge condition for the high-risk PE/DVT case before completing patient education._

`  133258ms` ← `supervisor` done in 3667.6ms — `{"next_agent": "summary", "step": 3}`

`  133259ms` **ROUTE** `route_from_supervisor` → **`summary`**  
    _supervisor selected 'summary'_


### `  133259ms` → node `summary`

`  137592ms` **AGENTIC RAG** the agent chose to look up:  
    _"discharge summary format protocol pending results disposition"_ → ['pneumonia_discharge_protocol.md', 'followup_scheduling_standards.md', 'pneumonia_discharge_protocol.md']

`  137592ms` **TOOL** `search_clinical_guidance` (local, ok)  
    args: `{"query": "discharge summary format protocol pending results disposition", "k": 3}`  
    result: `['Pending results', 'Ownership of pending results', 'Community-Acquired Pneumonia Discharge Protocol']`

`  146962ms` **REACT_COMPLETE** `{"worker": "summary", "iterations": 2, "reason": "model_stopped_calling_tools"}`

`  153409ms` **STRUCTURED_OUTPUT** `{"node": "summary", "schema": "DischargeSummary", "schema_valid": true, "error": ""}`

`  153412ms` ← `summary` done in 20153.1ms — `{"ok": true, "worker": "summary", "schema": "DischargeSummary"}`


### `  153414ms` → node `reflect`

`  158126ms` **STRUCTURED_OUTPUT** `{"node": "reflect", "schema": "Reflection", "schema_valid": true, "error": ""}`

`  158126ms` **CRITIC** `summary` confidence **0.95** → **ACCEPT**

`  158127ms` ← `reflect` done in 4713.2ms — `{"action": "accept", "confidence": 0.95}`

`  158127ms` **ROUTE** `route_after_reflection` → **`supervisor`**  
    _critic accepted or escalated; returning to supervisor_


### `  158128ms` → node `supervisor`

`  161048ms` **STRUCTURED_OUTPUT** `{"node": "supervisor", "schema": "SupervisorPlan", "schema_valid": true, "error": ""}`

`  161048ms` **SUPERVISOR** dispatches to **`education`**  
    _Medication, followup, and summary workstreams are all complete. The dependencies for patient education (medication and followup) are met, so education is scheduled next before case finalization._

`  161049ms` ← `supervisor` done in 2921.1ms — `{"next_agent": "education", "step": 4}`

`  161049ms` **ROUTE** `route_from_supervisor` → **`education`**  
    _supervisor selected 'education'_


### `  161050ms` → node `education`

`  164379ms` **AGENTIC RAG** the agent chose to look up:  
    _"patient education guidance for warfarin interactions fluconazole amiodarone red flag symptoms"_ → ['anticoagulation_guidance.md', 'anticoagulation_guidance.md', 'anticoagulation_guidance.md']

`  164379ms` **TOOL** `search_clinical_guidance` (local, ok)  
    args: `{"query": "patient education guidance for warfarin interactions fluconazole amiodarone red flag symptoms", "k": 3}`  
    result: `['Warfarin interactions at discharge', 'Patient counselling', 'When a new interacting medication is started']`

`  178205ms` **REACT_COMPLETE** `{"worker": "education", "iterations": 2, "reason": "model_stopped_calling_tools"}`

`  188688ms` **STRUCTURED_OUTPUT** `{"node": "education", "schema": "EducationPacket", "schema_valid": true, "error": ""}`

`  188689ms` ← `education` done in 27638.8ms — `{"ok": true, "worker": "education", "schema": "EducationPacket"}`


### `  188690ms` → node `reflect`

`  196063ms` **STRUCTURED_OUTPUT** `{"node": "reflect", "schema": "Reflection", "schema_valid": true, "error": ""}`

`  196064ms` **CRITIC** `education` confidence **0.95** → **ACCEPT**

`  196064ms` ← `reflect` done in 7374.2ms — `{"action": "accept", "confidence": 0.95}`

`  196065ms` **ROUTE** `route_after_reflection` → **`supervisor`**  
    _critic accepted or escalated; returning to supervisor_


### `  196066ms` → node `supervisor`

`  196066ms` **SUPERVISOR** dispatches to **`finalize`**  
    _all_complete_

`  196067ms` ← `supervisor` done in 0.7ms — `{"next_agent": "finalize", "step": 5}`

`  196067ms` **ROUTE** `route_from_supervisor` → **`finalize`**  
    _supervisor selected 'finalize'_


### `  196068ms` → node `finalize`

`  196275ms` **MEMORY_EVICTION** `{"namespace_hash": "pii_2c57f681d7", "dry_run": false, "evaluated": 19, "kept": 19, "evicted": 0, "expired_by_ttl": 0, "evicted_by_capacity": 0, "permanent_retained": 7, "evicted_keys": []}`

`  196275ms` **MEMORY_OP** `{"op": "write", "tier": "multi", "count": 15, "keys": ["allergy:sulfa drugs", "caregiver", "risk_tier", "admission:CASE-003", "med_change:CASE-003:warfarin", "med_change:CASE-003:amiodarone", "med_change:CASE-003:fluconazole", "interaction:warfarin+amiodarone", "interaction:warfarin+fluconazole", "unreconciled:omeprazole 20 mg once daily", "followup:CASE-003:anticoagulation clinic", "followup:CASE`

`  196275ms` **PACKET_FINALIZED** `{"completeness": 1.0, "escalations": 2, "quarantine_flags": 0, "workstreams_completed": ["medication", "followup", "summary", "education"]}`

`  196275ms` ← `finalize` done in 207.9ms — `{"completeness": 1.0}`


---

## Event summary

| event | count |
|---|---|
| `node_enter` | 17 |
| `node_exit` | 17 |
| `structured_output` | 12 |
| `routing_decision` | 11 |
| `supervisor_decision` | 5 |
| `tool_call` | 5 |
| `worker_context` | 4 |
| `react_complete` | 4 |
| `worker_output` | 4 |
| `reflection` | 4 |
| `rag_query` | 3 |
| `memory_op` | 2 |
| `tool_failure` | 2 |
| `mcp_connected` | 1 |
| `toolbox_ready` | 1 |
| `checkpoint_cleared` | 1 |
| `run_start` | 1 |
| `risk_assessment` | 1 |
| `human_in_the_loop` | 1 |
| `enhanced_pathway_applied` | 1 |
| `memory_eviction` | 1 |
| `packet_finalized` | 1 |
| `run_complete` | 1 |

Raw trace: [`case_003_faultinject.jsonl`](../traces/case_003_faultinject.jsonl)
