# AC-05 — Process 2: Resume from the Checkpoint

**Criteria:** AC-05 (deterministically scored)  
**Generated:** 2026-09-12T23:18:42+00:00  
**Regenerate:** `python scripts/generate_evidence.py`

> All data is synthetic. Identifiers are pseudonymised in traces (NFR-05).

---

A **different OS process** loads the checkpoint by `thread_id` and continues. The
recovered state — completed workstreams, supervisor step count, risk tier — came off
disk, not from memory: nothing was shared between the two processes.

```
$ python -m discharge_copilot resume --case-id CASE-003

                                                    │
│ State recovered from the previous process:                                   │
│   completed workstreams : []                                                 │
│   supervisor steps      : 1                                                  │
│   escalations           : 0                                                  │
│   risk tier             : high                                               │
╰──────────────────────────────────────────────────────────────────────────────╯

─────────────────────────────── Discharge packet ───────────────────────────────
╭────────────────────────────────── Summary ───────────────────────────────────╮
│ Deep vein thrombosis of the left lower extremity with pulmonary embolism     │
│                                                                              │
│ The patient is a 68-year-old male admitted with left calf swelling and       │
│ pleuritic chest pain. Doppler ultrasound confirmed an extensive left         │
│ femoropopliteal deep vein thrombosis (DVT), and CT pulmonary angiography     │
│ demonstrated bilateral segmental pulmonary emboli without right heart        │
│ strain. The patient remained hemodynamically stable throughout admission.    │
│ During the hospital stay, he developed rapid atrial fibrillation with rates  │
│ to 150 bpm, which settled after initiating amiodarone for rhythm control.    │
│ Following a course of inhaled steroids, he developed oropharyngeal           │
│ candidiasis and was started on a 7-day course of fluconazole.                │
│ Anticoagulation was continued with warfarin, with an INR of 2.4 and rising   │
│ on the day of discharge and dose increased to 7.5 mg daily. Because both     │
│ amiodarone and fluconazole substantially potentiate warfarin, the            │
│ anticoagulation clinic was asked to review the regimen urgently before the   │
│ first post-discharge dose, and INR must be rechecked within 3 days.          │
│ Additionally, the patient reported omeprazole was stopped during admission   │
│ without clear documentation, requiring outpatient clarification.             │
│                                                                              │
│ Condition at discharge: Hemodynamically stable. Rapid atrial fibrillation    │
│ rate-controlled on amiodarone. Oropharyngeal candidiasis improving on        │
│ fluconazole. Anticoagulated with INR 2.4 and rising on discharge day.        │
│ Disposition: home                                                            │
╰──────────────────────────────────────────────────────────────────────────────╯
                           Medication reconciliation                            
┏━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Medication   ┃ Action   ┃ Reason                                             ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Warfarin     │ modify   │ Dose increased from 5 mg once daily to 7.5 mg once │
│              │          │ daily during admission for INR management.         │
│ Amiodarone   │ start    │ Initiated during admission for rapid atrial        │
│              │          │ fibrillation rate/rhythm control.                  │
│ Fluconazole  │ start    │ Initiated for a 7-day course for oropharyngeal     │
│              │          │ candidiasis.                                       │
│ Tiotropium   │ continue │ Continued unchanged from pre-admission regimen.    │
│ Atorvastatin │ continue │ Continued from pre-admission regimen; requires     │
│              │          │ monitoring or dose adjustment due to               │
│              │          │ co-administration with amiodarone.                 │
│ Omeprazole   │ stop     │ Discontinued during admission without documented   │
│              │          │ clinical rationale or discharge instructions.      │
└──────────────┴──────────┴────────────────────────────────────────────────────┘
  ⚠ CONTRAINDICATED Fluconazole + Warfarin: Fluconazole strongly inhibits 
CYP2C9, the main enzyme clearing S-warfarin. INR can rise sharply within 2-3 
days, with a substantial bleeding risk.
  ⚠ MAJOR Amiodarone + Warfarin: Amiodarone inhibits warfarin metabolism and its
long half-life means the effect accumulates over weeks. INR typically rises with
a delayed onset.
  ⚠ MODERATE Amiodarone + Atorvastatin: Amiodarone inhibits CYP3A4, raising 
atorvastatin exposure and the risk of myopathy.
  Unreconciled: Omeprazole 20 mg once daily
                                 Follow-up plan                                 
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Specialty              ┃ Within ┃ Reason                                     ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Anticoagulation Clinic │     3d │ Urgent INR recheck and warfarin monitoring │
│                        │        │ due to severe drug interaction with        │
│                        │        │ fluconazole and amiodarone post-PE/DVT     │
│                        │        │ discharge.                                 │
│ Primary Care           │     6d │ High readmission risk discharge follow-up  │
│                        │        │ post-PE/DVT and AFib management within 7   │
│                        │        │ days per enhanced pathway.                 │
│ Cardiology             │    14d │ Follow-up for pulmonary embolism, atrial   │
│                        │        │ fibrillation, and amiodarone initiation.   │
└────────────────────────┴────────┴────────────────────────────────────────────┘
  Enhanced follow-up pathway applied
  home service: 48-72 hour post-discharge telephone check
  home service: home health nursing assessment within 5 days
  home service: pharmacist medication review call within 7 days
╭───────────────────────────── Patient education ──────────────────────────────╮
│ You were in the hospital for a blood clot in your leg and a blood clot in    │
│ your lungs, an irregular heartbeat, and a mouth infection. You are going     │
│ home with medicines to thin your blood, control your heart rhythm, treat     │
│ your mouth infection, and help your breathing. Because your blood thinner    │
│ interacts with your other medicines, you must get your blood checked very    │
│ soon to make sure your dose is safe.                                         │
│                                                                              │
│ Seek help immediately if:                                                    │
│   • You see bright red blood in your stool or your stool looks black like    │
│ tar                                                                          │
│   • You throw up blood or dark stuff that looks like coffee grounds          │
│   • You have a nosebleed that does not stop after 10 minutes of pressure     │
│   • You have sudden chest pain or sudden trouble breathing                   │
│   • You cough up blood                                                       │
│   • You notice new or worse pain, swelling, or warmth in your leg            │
│   • You feel dizzy, faint, or very weak                                      │
╰──────────────────────────────────────────────────────────────────────────────╯
╭──────────────────────────────── Escalations ─────────────────────────────────╮
│ • PHARMACIST REVIEW REQUIRED: Fluconazole + Warfarin (contraindicated);      │
│ Amiodarone + Warfarin (major). Discharge medications must not be dispensed   │
│ until signed off.                                                            │
│ • Enhanced follow-up pathway applied (readmission risk high, score 0.64).    │
╰──────────────────────────────────────────────────────────────────────────────╯

This packet is a draft coordination aid and requires clinician approval before 
use.

Trace: /app/evidence/traces/case_003_resume.jsonl

```
