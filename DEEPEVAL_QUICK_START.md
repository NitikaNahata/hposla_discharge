# DeepEval Quick Start (Gemini 1.5 Pro) ⚡

**Status:** ✅ Fully implemented and ready to use

---

## **One-Command Setup** 🚀

```bash
# Run EVERYTHING (build + tests + evaluation + deepeval)
docker-compose up --build
```

That's it! This will:
1. Build Docker image
2. Run app (case_001)
3. Run pytest
4. Generate evidence
5. **Run DeepEval (Gemini 1.5 Pro)**
6. **Auto-commit results to git**

**Time:** ~3-5 minutes

---

## **Just Run DeepEval** 🧪

```bash
# Evaluate all committed cases
python evaluation/deepeval_runner.py

# Evaluate one case
python evaluation/deepeval_runner.py --case CASE-001

# Evaluate + commit to git
python evaluation/deepeval_runner.py --commit
```

**Output:** `evidence/deepeval_report.json`

---

## **What Gets Committed** 📝

After running:
```
evidence/deepeval_report.json
├─ Timestamp
├─ Model used: gemini-1.5-pro
├─ Average score: 0.87 (B grade)
└─ Per-case metrics:
   ├─ Faithfulness (grounded?)
   ├─ Hallucination (made up facts?)
   └─ Relevancy (answers question?)
```

**Git commit message:**
```
docs: add DeepEval assessment report (Gemini 1.5 Pro)

Average score: 0.87
Model: gemini-1.5-pro
Timestamp: 2026-09-12T10:30:45+00:00
```

---

## **Pipeline Flow** 🔄

```
docker-compose up --build
        ↓
┌─────────────────────────┐
│ 1. build_docker         │ ✅ Build image
│ 2. quality              │ ✅ Lint + type
│ 3. test (py311/312)     │ ✅ Unit tests
│ 4. evaluation           │ ✅ Rule-based
│ 5. deepeval ← NEW!      │ ✅ LLM (Gemini Pro)
│ 6. reproducibility      │ ✅ Clean install
└─────────────────────────┘
        ↓
All results → evidence/
All committed → git history
```

---

## **Metrics Explained** 📊

| Metric | Checks | Example |
|---|---|---|
| **Faithfulness** | Grounded in source? | "Meds match patient records?" |
| **Hallucination** | Made up facts? | "Invented drug doses?" |
| **Relevancy** | Answers question? | "Addresses patient needs?" |

**Scores:**
- ✅ 0.90-1.0 = Excellent (A)
- ✅ 0.80-0.89 = Good (B)
- ⚠️ 0.70-0.79 = Acceptable (C)
- ❌ <0.70 = Needs work (D)

---

## **GitHub/GitLab Workflow** 🔄

```bash
# 1. Make changes
vim src/discharge_copilot/nodes/workers.py

# 2. Test locally
docker-compose up --build

# 3. Commit (with DeepEval results)
git log -1
# Shows: "docs: add DeepEval assessment report"
# Shows: evidence/deepeval_report.json committed

# 4. Push
git push origin main

# 5. Monitor GitLab CI
# Goes to: Project → CI/CD → Pipelines
# Stages run automatically including deepeval_assessment
```

---

## **Files Created/Modified** 📁

```
✅ evaluation/deepeval_runner.py       (NEW) - DeepEval evaluation
✅ requirements.txt                    (UPDATED) - Added deepeval
✅ docker-compose.yml                  (UPDATED) - Added deepeval service
✅ .gitlab-ci.yml                      (UPDATED) - Added deepeval stage
✅ docs/DEEPEVAL_SETUP.md              (NEW) - Complete guide
✅ DEEPEVAL_QUICK_START.md             (NEW) - This file
```

---

## **Why Gemini 1.5 Pro?** 🤖

```
gemini-1.5-flash (fast but less accurate):
  - Speed: ⚡ Fast (2-3s)
  - Hallucination detection: ⚠️ Good
  - Cost: 💰 Cheaper
  - Use: Quick checks

gemini-1.5-pro (slower but more accurate) ⭐ CHOSEN:
  - Speed: 🐢 Slower (5-10s)
  - Hallucination detection: ✅ Better
  - Cost: 💸 2-3x cost
  - Use: Quality assurance (healthcare!)
```

For medical evaluations → accuracy matters → Pro is worth it

---

## **Example Output** 📋

```json
{
  "timestamp": "2026-09-12T10:30:45.123456+00:00",
  "model": "gemini-1.5-pro",
  "average_score": 0.87,
  "grade": "B",
  "cases": {
    "CASE-001": {
      "overall_score": 0.88,
      "pass_rate": 0.80,
      "evaluations": [
        {
          "metric": "Faithfulness",
          "worker": "medication",
          "score": 0.92,
          "is_pass": true,
          "reasoning": "Medication reconciliation is well-grounded in clinical notes..."
        },
        {
          "metric": "Hallucination",
          "worker": "medication",
          "score": 0.95,
          "is_pass": true,
          "reasoning": "No fabricated medications detected..."
        }
      ]
    }
  }
}
```

---

## **Troubleshooting** 🔧

| Issue | Fix |
|---|---|
| **DeepEval import error** | `pip install deepeval` |
| **API key not found** | `export GOOGLE_API_KEY="..."` |
| **Git commit fails** | Check `.git` mounted in docker-compose |
| **Rate limiting** | DeepEval retries automatically |

---

## **Cost Estimate** 💰

Per full run (4 cases):
- **Calls to Gemini:** ~12-16 API calls
- **Time:** 60-90 seconds
- **Cost:** ~$0.10-0.15
- **Frequency:** Once per commit to main

---

## **Integration Timeline** ⏱️

```
Before: docker-compose up --build
  └─ 2-3 minutes
  └─ No DeepEval

After: docker-compose up --build
  ├─ 3-5 minutes (+ ~1-2 min for DeepEval)
  ├─ Includes Gemini 1.5 Pro evaluation
  ├─ Results committed automatically
  └─ Full quality assurance
```

---

## **Next: Push to Production** 🚀

```bash
# 1. Everything in docker-compose works
docker-compose up --build
# ✅ All stages pass
# ✅ DeepEval runs
# ✅ Results committed

# 2. Push to GitHub/GitLab
git push origin main

# 3. CI/CD runs automatically
# Monitor in GitHub Actions or GitLab CI/CD

# 4. Check results
git log -1 --stat
# Shows deepeval_report.json committed
```

---

## **Summary** ✅

| Feature | Status | Details |
|---|---|---|
| **DeepEval impl** | ✅ Done | evaluation/deepeval_runner.py |
| **Gemini 1.5 Pro** | ✅ Configured | Uses Pro for accuracy |
| **Auto-commit** | ✅ Enabled | Commits results to git |
| **Docker integration** | ✅ Added | docker-compose service |
| **GitLab CI** | ✅ Updated | .gitlab-ci.yml stage added |
| **Documentation** | ✅ Complete | docs/DEEPEVAL_SETUP.md |

**Everything is ready to go!** 🎉

---

## **One Final Command** 🎯

```bash
docker-compose up --build
```

This runs your complete evaluation pipeline with:
- Deterministic checks (fast, no cost)
- DeepEval LLM-based checks (accurate, with Gemini 1.5 Pro)
- Automatic git commit of results
- Full CI/CD integration

**That's it!** ⚡
