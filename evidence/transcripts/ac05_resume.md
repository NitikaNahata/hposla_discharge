# AC-05 — Process 2: Resume from the Checkpoint

**Criteria:** AC-05 (deterministically scored)  
**Generated:** 2026-09-09T19:37:44+00:00  
**Regenerate:** `python scripts/generate_evidence.py`

> All data is synthetic. Identifiers are pseudonymised in traces (NFR-05).

---

A **different OS process** loads the checkpoint by `thread_id` and continues. The
recovered state — completed workstreams, supervisor step count, risk tier — came off
disk, not from memory: nothing was shared between the two processes.

```
$ python -m discharge_copilot resume --case-id CASE-003

                                    │
│   escalations           : 0                                                  │
│   risk tier             : high                                               │
╰──────────────────────────────────────────────────────────────────────────────╯

─────────────────────────────── Discharge packet ───────────────────────────────
╭────────────────────────────────── Summary ───────────────────────────────────╮
│ Deep vein thrombosis of the left lower extremity with pulmonary embolism     │
│                                                                              │
│ The patient is a 68-year-old male with COPD admitted on September 1, 2026,   │
│ presenting with left calf swelling and pleuritic chest pain. Doppler         │
│ ultrasound confirmed an extensive left femoropopliteal deep vein thrombosis, │
│ and CT pulmonary angiography demonstrated bilateral segmental pulmonary      │
│ emboli without right heart strain. He remained hemodynamically stable        │
│ throughout admission. Anticoagulation was continued with warfarin, and the   │
│ dose was increased to 7.5 mg daily, with an INR of 2.4 and rising at         │
│ discharge. During admission, he developed rapid atrial fibrillation with     │
│ rates to 150, which settled after amiodarone initiation. He also developed   │
│ oropharyngeal candidiasis following inhaled steroid use and was started on a │
│ seven-day course of fluconazole. Given that both amiodarone and fluconazole  │
│ potentiate warfarin, the anticoagulation clinic was asked to urgently review │
│ the regimen before the first post-discharge dose, with an INR recheck        │
│ required within 3 days. Pre-admission omeprazole was held during admission   │
│ and remains unreconciled for outpatient decision.                            │
│                                                                              │
│ Condition at discharge: Hemodynamically stable, rate-controlled on           │
│ amiodarone, INR 2.4 and rising. Discharged home with son assisting with home │
│ medication schedule.                                                         │
│ Disposition: home                                                            │
╰──────────────────────────────────────────────────────────────────────────────╯
                           Medication reconciliation                            
┏━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Medication   ┃ Action   ┃ Reason                                             ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Warfarin     │ modify   │ Dose increased from 5 mg to 7.5 mg once daily      │
│              │          │ during admission. Requires urgent pharmacist       │
│              │          │ review due to severe interactions with fluconazole │
│              │          │ and amiodarone.                                    │
│ Tiotropium   │ continue │ Continued at pre-admission dose.                   │
│ Atorvastatin │ continue │ Continued at pre-admission dose. Interaction with  │
│              │          │ amiodarone noted.                                  │
│ Amiodarone   │ start    │ Initiated during admission for rapid atrial        │
│              │          │ fibrillation.                                      │
│ Fluconazole  │ start    │ Initiated during admission for a 7-day course for  │
│              │          │ oropharyngeal candidiasis.                         │
└──────────────┴──────────┴────────────────────────────────────────────────────┘
  ⚠ CONTRAINDICATED Fluconazole + Warfarin: Fluconazole strongly inhibits 
CYP2C9, increasing warfarin exposure and INR sharply with high bleeding risk.
  ⚠ MAJOR Amiodarone + Warfarin: Amiodarone inhibits warfarin metabolism, 
increasing warfarin concentration and INR.
  ⚠ MODERATE Amiodarone + Atorvastatin: Amiodarone inhibits CYP3A4, raising 
atorvastatin exposure and myopathy risk.
  Unreconciled: Omeprazole 20 mg once daily
                                 Follow-up plan                                 
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Specialty               ┃ Within ┃ Reason                                    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Anticoagulation Clinic  │     3d │ Urgent INR monitoring due to warfarin     │
│                         │        │ dosage escalation and                     │
│                         │        │ major/contraindicated interactions with   │
│                         │        │ fluconazole and amiodarone.               │
│ Post-Discharge Outreach │     3d │ 48-72 hour telephone outreach check under │
│                         │        │ enhanced high-risk pathway to assess      │
│                         │        │ post-discharge clinical stability and     │
│                         │        │ check for signs of bleeding.              │
│ Primary Care            │     7d │ Primary care follow-up for high-risk      │
│                         │        │ discharge (PE/DVT, AFib, COPD) and        │
│                         │        │ overall medication reconciliation.        │
└─────────────────────────┴────────┴───────────────────────────────────────────┘
  Enhanced follow-up pathway applied
  home service: Home Health Nursing for complex medication education and 
monitoring
  home service: 48-72 hour post-discharge telephone check
  home service: home health nursing assessment within 5 days
  home service: pharmacist medication review call within 7 days
╭───────────────────────────── Patient education ──────────────────────────────╮
│ You were in the hospital because of a blood clot in your left leg and lungs, │
│ a fast heartbeat, and a mouth infection called thrush. You are going home    │
│ with 5 main medicines. Your blood thinner dose was increased to 7.5 mg       │
│ daily. Because your new medicines interact with each other, you must get     │
│ your blood checked on September 9, 2026. You will also get a check-in phone  │
│ call on September 8, 2026, and see your primary care doctor on September 12, │
│ 2026.                                                                        │
│                                                                              │
│ Seek help immediately if:                                                    │
│   • Bleeding that does not stop after 10 minutes of direct pressure on the   │
│ cut.                                                                         │
│   • Blood in your urine (pink, red, or dark brown) or blood in your stool    │
│ (bright red or dark black tar-like stool).                                   │
│   • Coughing up blood or vomiting stuff that looks like dark coffee grounds. │
│   • Sudden chest pain or severe trouble breathing - call 911 immediately.    │
│   • New or worsening pain, redness, or swelling in your left leg.            │
│   • Gaining more than 3 pounds in one day.                                   │
╰──────────────────────────────────────────────────────────────────────────────╯
╭──────────────────────────────── Escalations ─────────────────────────────────╮
│ • PHARMACIST REVIEW REQUIRED: Fluconazole + Warfarin (contraindicated);      │
│ Amiodarone + Warfarin (major). Discharge medications must not be dispensed   │
│ until signed off.                                                            │
│ • Enhanced follow-up pathway applied (readmission risk high, score 0.64).    │
│ • Enhanced follow-up pathway applied (readmission risk high, score 0.64).    │
│ • Enhanced follow-up pathway applied (readmission risk high, score 0.64).    │
╰──────────────────────────────────────────────────────────────────────────────╯

This packet is a draft coordination aid and requires clinician approval before 
use.

Trace: 
/Users/nitikajain/Downloads/final_capstone/evidence/traces/case_003_resume.jsonl

```
