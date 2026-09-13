# AC-12 — Self-Healing After a Forced Tool Failure

**Criteria:** AC-12 (reflection / self-healing) · NFR-07 (graceful degradation)  
**Generated:** 2026-09-12T23:18:42+00:00  
**Regenerate:** `python scripts/generate_evidence.py`

> All data is synthetic. Identifiers are pseudonymised in traces (NFR-05).

---

`--fault-inject mcp_timeout` makes the MCP server stall past the client
timeout. This is a **real** failure in the real transport, not a simulated
one: the server process genuinely does not answer.

The run degrades to local context, records the failure, and still produces a
usable packet — which is what NFR-07 asks for.

## Run timeline


### `     347ms` → node `intake`

`     348ms` **RISK** tier **high** (score 0.64) — readmitted within the last 12 months, 5 or more discharge medications, three or more active problems, diagnosis with a high 30-day readmission rate

`    3846ms` **MEMORY_OP** `{"op": "recall", "tier": "multi", "count": 8, "keys": ["allergy:sulfa drugs", "interaction:warfarin+amiodarone", "interaction:amiodarone+warfarin", "risk_tier", "admission:CASE-003", "followup:CASE-003:cardiology", "followup:CASE-003:anticoagulation clinic", "interaction:fluconazole+warfarin"], "cross_session": false}`

`    3847ms` ← `intake` done in 3499.8ms — `{"risk_tier": "high", "notes_quarantined": 4, "notes_flagged": 0, "memories_recalled": 8}`


### `    3848ms` → node `supervisor`

`    7280ms` **STRUCTURED_OUTPUT** `{"node": "supervisor", "schema": "SupervisorPlan", "schema_valid": true, "error": ""}`

`    7280ms` **SUPERVISOR** dispatches to **`medication`**  
    _High readmission risk for PE/DVT requires immediate medication reconciliation regarding anticoagulation therapy, which is also a legal prerequisite for scheduling follow-up and patient education._

`    7280ms` ← `supervisor` done in 3431.9ms — `{"next_agent": "medication", "step": 1}`

`    7281ms` **ROUTE** `route_from_supervisor` → **`medication`**  
    _supervisor selected 'medication'_


### `    7282ms` → node `medication`

`   41992ms` **TOOL** `medication_interaction_check` (mcp, FAILED)  
    args: `{"medications": ["Warfarin", "Amiodarone", "Fluconazole", "Tiotropium", "Atorvastatin"]}`  
    result: `TOOL TIMEOUT: 'medication_interaction_check' did not respond within 30s. The hospital system may be unavailable. Proceed using the case data you already have and record that this check could not be completed.`

`   41999ms` **TOOL_FAILURE** `{"tool": "medication_interaction_check", "source": "mcp", "error": "timeout", "timeout_seconds": 30.0, "recovery": "degraded_to_local_context"}`

`   54882ms` **REACT_COMPLETE** `{"worker": "medication", "iterations": 2, "reason": "model_stopped_calling_tools"}`

`   65382ms` **STRUCTURED_OUTPUT** `{"node": "medication", "schema": "MedicationReconciliation", "schema_valid": true, "error": ""}`

`   65382ms` ← `medication` done in 58100.5ms — `{"ok": true, "worker": "medication", "schema": "MedicationReconciliation"}`

`   65392ms` **ROUTE** `route_after_medication` → **`pharmacist_review`**  
    _highest interaction severity=major; pharmacist_required=True_


### `   65408ms` → node `pharmacist_review`

`   65409ms` **HUMAN_IN_THE_LOOP** `{"node": "pharmacist_review", "interactions": 3, "serious": 3, "detail": "Warfarin + Amiodarone (major); Warfarin + Fluconazole (major); Amiodarone + Atorvastatin (major)", "note": "Graph interrupted here; state checkpointed for pharmacist sign-off."}`

`   65409ms` ← `pharmacist_review` done in 1.3ms — `{"serious_interactions": 3}`


### `   65410ms` → node `reflect`

`   71480ms` **STRUCTURED_OUTPUT** `{"node": "reflect", "schema": "Reflection", "schema_valid": true, "error": ""}`

`   71481ms` **CRITIC** `medication` confidence **0.95** → **ACCEPT**

`   71481ms` ← `reflect` done in 6071.0ms — `{"action": "accept", "confidence": 0.95}`

`   71482ms` **ROUTE** `route_after_reflection` → **`supervisor`**  
    _critic accepted or escalated; returning to supervisor_


### `   71483ms` → node `supervisor`

`   74918ms` **STRUCTURED_OUTPUT** `{"node": "supervisor", "schema": "SupervisorPlan", "schema_valid": true, "error": ""}`

`   74919ms` **SUPERVISOR** dispatches to **`followup`**  
    _Medication reconciliation is complete. Given the high readmission risk and primary diagnosis of DVT with pulmonary embolism, setting up follow-up appointments and anticoagulation monitoring is clinically urgent and required to unblock patient education._

`   74919ms` ← `supervisor` done in 3436.1ms — `{"next_agent": "followup", "step": 2}`

`   74920ms` **ROUTE** `route_from_supervisor` → **`followup`**  
    _supervisor selected 'followup'_


### `   74920ms` → node `followup`

`   79661ms` **AGENTIC RAG** the agent chose to look up:  
    _"high risk discharge follow-up protocol warfarin INR monitoring interactions"_ → ['anticoagulation_guidance.md', 'anticoagulation_guidance.md', 'pneumonia_discharge_protocol.md']

`   79662ms` **TOOL** `search_clinical_guidance` (local, ok)  
    args: `{"query": "high risk discharge follow-up protocol warfarin INR monitoring interactions", "k": 3}`  
    result: `['When a new interacting medication is started', 'Warfarin interactions at discharge', 'Antibiotic interactions at discharge']`

`  112083ms` **TOOL** `schedule_followup` (mcp, FAILED)  
    args: `{"within_days": 3, "mrn": "CASE-003", "specialty": "Anticoagulation Clinic", "reason": "Urgent INR recheck and warfarin dose adjustment due to major drug interactions with newly started Amiodarone and`  
    result: `TOOL TIMEOUT: 'schedule_followup' did not respond within 30s. The hospital system may be unavailable. Proceed using the case data you already have and record that this check could not be completed.`

`  112089ms` **TOOL_FAILURE** `{"tool": "schedule_followup", "source": "mcp", "error": "timeout", "timeout_seconds": 30.0, "recovery": "degraded_to_local_context"}`

`  121523ms` **REACT_COMPLETE** `{"worker": "followup", "iterations": 3, "reason": "model_stopped_calling_tools"}`

`  126813ms` **STRUCTURED_OUTPUT** `{"node": "followup", "schema": "FollowUpPlan", "schema_valid": true, "error": ""}`

`  126814ms` ← `followup` done in 51893.6ms — `{"ok": true, "worker": "followup", "schema": "FollowUpPlan"}`

`  126816ms` **ROUTE** `route_risk_tier` → **`enhanced_followup`**  
    _readmission risk tier=high (score 0.64)_


### `  126819ms` → node `enhanced_followup`

`  126820ms` **ENHANCED_PATHWAY_APPLIED** `{"risk_tier": "high", "risk_score": 0.64, "services_added": ["48-72 hour post-discharge telephone check", "home health nursing assessment within 5 days", "pharmacist medication review call within 7 days"]}`

`  126821ms` ← `enhanced_followup` done in 1.8ms — `{"services_added": 3}`


### `  126821ms` → node `reflect`

`  132118ms` **STRUCTURED_OUTPUT** `{"node": "reflect", "schema": "Reflection", "schema_valid": true, "error": ""}`

`  132118ms` **CRITIC** `followup` confidence **0.95** → **ACCEPT**

`  132119ms` ← `reflect` done in 5297.4ms — `{"action": "accept", "confidence": 0.95}`

`  132119ms` **ROUTE** `route_after_reflection` → **`supervisor`**  
    _critic accepted or escalated; returning to supervisor_


### `  132121ms` → node `supervisor`

`  135715ms` **STRUCTURED_OUTPUT** `{"node": "supervisor", "schema": "SupervisorPlan", "schema_valid": true, "error": ""}`

`  135716ms` **SUPERVISOR** dispatches to **`summary`**  
    _Medication and follow-up workstreams are complete, fulfilling prerequisites for education. Selecting summary next to document the hospital course and clinical condition before finalizing patient education instructions._

`  135716ms` ← `supervisor` done in 3595.2ms — `{"next_agent": "summary", "step": 3}`

`  135716ms` **ROUTE** `route_from_supervisor` → **`summary`**  
    _supervisor selected 'summary'_


### `  135717ms` → node `summary`

`  139916ms` **AGENTIC RAG** the agent chose to look up:  
    _"discharge summary guidance required sections"_ → ['patient_education_principles.md', 'pneumonia_discharge_protocol.md', 'anticoagulation_guidance.md']

`  139916ms` **TOOL** `search_clinical_guidance` (local, ok)  
    args: `{"query": "discharge summary guidance required sections", "k": 3}`  
    result: `['Reading level', 'Pre-discharge requirements', 'Anticoagulation Discharge Guidance']`

`  140823ms` **AGENTIC RAG** the agent chose to look up:  
    _"anticoagulation discharge guidance warfarin amiodarone fluconazole"_ → ['anticoagulation_guidance.md', 'anticoagulation_guidance.md', 'anticoagulation_guidance.md']

`  140824ms` **TOOL** `search_clinical_guidance` (local, ok)  
    args: `{"query": "anticoagulation discharge guidance warfarin amiodarone fluconazole", "k": 3}`  
    result: `['Warfarin interactions at discharge', 'Direct oral anticoagulants', 'Patient counselling']`

`  153395ms` **REACT_COMPLETE** `{"worker": "summary", "iterations": 3, "reason": "model_stopped_calling_tools"}`

`  158279ms` **STRUCTURED_OUTPUT** `{"node": "summary", "schema": "DischargeSummary", "schema_valid": true, "error": ""}`

`  158280ms` ← `summary` done in 22562.5ms — `{"ok": true, "worker": "summary", "schema": "DischargeSummary"}`


### `  158282ms` → node `reflect`

`  166199ms` **STRUCTURED_OUTPUT** `{"node": "reflect", "schema": "Reflection", "schema_valid": true, "error": ""}`

`  166199ms` **CRITIC** `summary` confidence **0.95** → **ACCEPT**

`  166200ms` ← `reflect` done in 7917.3ms — `{"action": "accept", "confidence": 0.95}`

`  166200ms` **ROUTE** `route_after_reflection` → **`supervisor`**  
    _critic accepted or escalated; returning to supervisor_


### `  166201ms` → node `supervisor`

`  168175ms` **STRUCTURED_OUTPUT** `{"node": "supervisor", "schema": "SupervisorPlan", "schema_valid": true, "error": ""}`

`  168175ms` **SUPERVISOR** dispatches to **`education`**  
    _Medication, followup, and summary workstreams are completed. Education is the final remaining workstream and its dependencies are now met._

`  168176ms` ← `supervisor` done in 1974.3ms — `{"next_agent": "education", "step": 4}`

`  168176ms` **ROUTE** `route_from_supervisor` → **`education`**  
    _supervisor selected 'education'_


### `  168178ms` → node `education`

`  171266ms` **AGENTIC RAG** the agent chose to look up:  
    _"warfarin blood thinner patient education bleeding red flags discharge guidance"_ → ['anticoagulation_guidance.md', 'anticoagulation_guidance.md', 'anticoagulation_guidance.md']

`  171266ms` **TOOL** `search_clinical_guidance` (local, ok)  
    args: `{"query": "warfarin blood thinner patient education bleeding red flags discharge guidance", "k": 3}`  
    result: `['Patient counselling', 'Warfarin interactions at discharge', 'When a new interacting medication is started']`

`  183707ms` **REACT_COMPLETE** `{"worker": "education", "iterations": 2, "reason": "model_stopped_calling_tools"}`

`  193251ms` **STRUCTURED_OUTPUT** `{"node": "education", "schema": "EducationPacket", "schema_valid": true, "error": ""}`

`  193252ms` ← `education` done in 25074.2ms — `{"ok": true, "worker": "education", "schema": "EducationPacket"}`


### `  193253ms` → node `reflect`

`  197172ms` **STRUCTURED_OUTPUT** `{"node": "reflect", "schema": "Reflection", "schema_valid": true, "error": ""}`

`  197173ms` **CRITIC** `education` confidence **0.95** → **ACCEPT**

`  197173ms` ← `reflect` done in 3920.5ms — `{"action": "accept", "confidence": 0.95}`

`  197174ms` **ROUTE** `route_after_reflection` → **`supervisor`**  
    _critic accepted or escalated; returning to supervisor_


### `  197175ms` → node `supervisor`

`  197176ms` **SUPERVISOR** dispatches to **`finalize`**  
    _all_complete_

`  197176ms` ← `supervisor` done in 1.1ms — `{"next_agent": "finalize", "step": 5}`

`  197176ms` **ROUTE** `route_from_supervisor` → **`finalize`**  
    _supervisor selected 'finalize'_


### `  197177ms` → node `finalize`

`  197865ms` **MEMORY_EVICTION** `{"namespace_hash": "pii_0f7a430333", "dry_run": false, "evaluated": 25, "kept": 25, "evicted": 0, "expired_by_ttl": 0, "evicted_by_capacity": 0, "permanent_retained": 8, "evicted_keys": []}`

`  197865ms` **MEMORY_OP** `{"op": "write", "tier": "multi", "count": 17, "keys": ["allergy:sulfa drugs", "caregiver", "risk_tier", "admission:CASE-003", "med_change:CASE-003:warfarin", "med_change:CASE-003:amiodarone", "med_change:CASE-003:fluconazole", "interaction:warfarin+amiodarone", "interaction:warfarin+fluconazole", "interaction:amiodarone+atorvastatin", "unreconciled:omeprazole 20 mg once daily", "followup:CASE-003:`

`  197865ms` **PACKET_FINALIZED** `{"completeness": 1.0, "escalations": 2, "quarantine_flags": 0, "workstreams_completed": ["medication", "followup", "summary", "education"]}`

`  197865ms` ← `finalize` done in 688.0ms — `{"completeness": 1.0}`


---

## Event summary

| event | count |
|---|---|
| `token_usage` | 22 |
| `node_enter` | 17 |
| `node_exit` | 17 |
| `structured_output` | 12 |
| `routing_decision` | 11 |
| `tool_call` | 6 |
| `supervisor_decision` | 5 |
| `worker_context` | 4 |
| `react_complete` | 4 |
| `worker_output` | 4 |
| `reflection` | 4 |
| `rag_query` | 4 |
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
