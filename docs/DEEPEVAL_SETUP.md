# DeepEval LLM-Based Assessment Guide

**Purpose:** High-quality evaluation using Gemini 1.5 Pro to detect:
- ✅ Hallucinations (agent making up facts)
- ✅ Grounding issues (claims not supported by source)
- ✅ Relevancy gaps (output doesn't answer question)

**Why Gemini 1.5 Pro?**
- Better at reasoning about complex medical evaluations
- More accurate hallucination detection
- Superior faithfulness checking
- Worth the extra cost for quality assurance

---

## **What DeepEval Tests** 🧪

### **Metric 1: Faithfulness**
**Question:** Is output grounded in source data?

**How it works:**
```python
from deepeval.metrics import Faithfulness

# "Is medication reconciliation supported by clinical notes?"
faithfulness = Faithfulness()
score = faithfulness.measure(
    input=clinical_notes,
    actual_output=medication_changes
)
# Returns: 0.0-1.0 (how well grounded)
```

**For your agent:**
- Medication: "All changes supported by notes?"
- Summary: "Clinical findings documented in records?"

---

### **Metric 2: Hallucination**
**Question:** Did agent invent facts not in source?

**How it works:**
```python
from deepeval.metrics import Hallucination

# "Did agent make up medication dosages?"
hallucination = Hallucination()
score = hallucination.measure(
    input=known_medications,
    actual_output=agent_changes
)
# Returns: 0.0-1.0 (lower = more hallucinations detected)
```

**For your agent:**
- Medication: "Any invented drug names or doses?"
- Followup: "Any fabricated appointment slots?"
- Education: "Any made-up symptoms or instructions?"

---

### **Metric 3: AnswerRelevancy**
**Question:** Does output address the question?

**How it works:**
```python
from deepeval.metrics import AnswerRelevancy

# "Does education answer 'how do I take my meds?'"
relevancy = AnswerRelevancy()
score = relevancy.measure(
    input="How should I take my medications?",
    actual_output=education_instructions
)
# Returns: 0.0-1.0 (how well it answers)
```

**For your agent:**
- Followup: "Do appointments address clinical needs?"
- Education: "Does it answer all patient questions?"

---

### **Metric 4: ContextualRelevancy** (Optional)
**Question:** Are retrieved documents relevant?

**How it works:**
```python
from deepeval.metrics import ContextualRelevancy

# "Are RAG results relevant to medication query?"
relevancy = ContextualRelevancy()
score = relevancy.measure(
    input="Check warfarin interactions",
    actual_output=retrieved_documents
)
```

---

## **Run DeepEval Locally** 🚀

### **Quick Start (all cases)**
```bash
# Evaluate all 4 committed cases
python evaluation/deepeval_runner.py

# Output: evidence/deepeval_report.json
```

### **Single Case**
```bash
python evaluation/deepeval_runner.py --case CASE-001
```

### **With Auto-Commit**
```bash
# Run evaluation and commit results to git
python evaluation/deepeval_runner.py --commit

# Creates commit: "docs: add DeepEval assessment report"
# Commits: evidence/deepeval_report.json
```

### **In Docker**
```bash
docker-compose up deepeval

# Runs after evidence generation
# Automatically commits results
```

---

## **Output Format** 📊

**Example: evidence/deepeval_report.json**

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
          "reasoning": "Medication reconciliation is well-grounded in clinical notes. All changes documented in the discharge summary."
        },
        {
          "metric": "Hallucination",
          "worker": "medication",
          "score": 0.95,
          "is_pass": true,
          "reasoning": "No fabricated medications detected. All doses match patient records."
        },
        {
          "metric": "AnswerRelevancy",
          "worker": "followup",
          "score": 0.85,
          "is_pass": true,
          "reasoning": "Follow-up plan addresses primary diagnosis and medication monitoring needs."
        },
        {
          "metric": "AnswerRelevancy",
          "worker": "education",
          "score": 0.88,
          "is_pass": true,
          "reasoning": "Education packet includes specific medication instructions and actionable red-flag symptoms."
        },
        {
          "metric": "Faithfulness",
          "worker": "summary",
          "score": 0.82,
          "is_pass": true,
          "reasoning": "Summary is grounded in clinical notes with minor gaps in detail about patient education received."
        }
      ]
    },
    "CASE-002": { ... },
    "CASE-003": { ... },
    "CASE-004": { ... }
  }
}
```

**Score Interpretation:**
- ✅ **0.90-1.0:** Excellent (A)
- ✅ **0.80-0.89:** Good (B)
- ⚠️ **0.70-0.79:** Acceptable (C)
- ❌ **<0.70:** Needs improvement (D)

---

## **Full Pipeline** 🔄

When you run `docker-compose up --build`:

```
1. build_docker
   └─ Build image

2. quality
   └─ Lint + type check

3. test_py311 / test_py312
   └─ Unit tests

4. evaluation
   └─ Deterministic checks (rule-based, no LLM)

5. deepeval (NEW!)
   ├─ Run Faithfulness checks (Gemini 1.5 Pro)
   ├─ Run Hallucination checks
   ├─ Run Relevancy checks
   ├─ Write: evidence/deepeval_report.json
   └─ Commit to git

6. reproducibility
   └─ Clean install + secret check
```

**Total time: ~3-5 minutes** (DeepEval adds ~1-2 min for Gemini API calls)

---

## **What Gets Committed** 📝

After `docker-compose up --build`:

```
git log --oneline
# Shows new commit:
# abc123d docs: add DeepEval assessment report (Gemini 1.5 Pro)
#             Average score: 0.87
#             Model: gemini-1.5-pro
#             Timestamp: 2026-09-12T...

git diff HEAD~1
# Shows:
# + evidence/deepeval_report.json
```

---

## **Cost & Speed** ⏱️💰

**Per evaluation run (4 cases):**
- **Time:** 60-90 seconds (Gemini 1.5 Pro API calls)
- **Cost:** ~$0.10-0.15 (depends on model pricing)
- **Calls:** 12-16 LLM calls (3-4 metrics × 4 cases)

**Gemini 1.5 Pro vs Flash:**
| Aspect | Flash | Pro |
|---|---|---|
| Speed | Fast (2-3s) | Slower (5-10s) |
| Accuracy | Good | Excellent ⭐ |
| Cost | Cheaper | 2-3x cost |
| Hallucination detection | Good | Better |
| Use case | Quick checks | Quality assurance |

**Why Pro for evaluation?**
- Evaluating clinical outputs → need accuracy
- Hallucination detection critical → Pro is better
- Cost acceptable for CI/CD → runs once per commit

---

## **GitLab CI/CD Integration** 🔄

Updated `.gitlab-ci.yml` now includes:

```yaml
deepeval_assessment:
  stage: deepeval
  script:
    - python evaluation/deepeval_runner.py --commit
  artifacts:
    - evidence/deepeval_report.json
  only:
    - main
```

**When it runs:**
- Every push to `main`
- After evaluation stage completes
- Before reproducibility check

**What it does:**
1. Runs DeepEval on all 4 cases
2. Generates report
3. Commits to git automatically
4. Saves artifact (retrievable in GitLab UI)

---

## **Troubleshooting** 🔧

### **API Key Issues**
```bash
# Check GOOGLE_API_KEY is set
echo $GOOGLE_API_KEY

# Set if missing
export GOOGLE_API_KEY="your-key-here"

# In docker-compose, ensure .env has it
cat .env | grep GOOGLE_API_KEY
```

### **Gemini Rate Limiting**
```bash
# If hitting rate limits, add delay
# DeepEval retries automatically, but you can add:
# sleep 1 between case evaluations
```

### **Git Commit Fails in Docker**
```bash
# Check git config in docker-compose:
# Dockerfile sets:
# git config --global user.email "ci@virtusa.com"
# git config --global user.name "GitLab CI"

# Verify:
docker exec discharge-copilot-deepeval git config user.name
```

### **DeepEval Import Error**
```bash
# Ensure deepeval is in requirements.txt
grep deepeval requirements.txt
# Should show: deepeval>=1.3.0,<2

# Reinstall
pip install --upgrade deepeval
```

---

## **Commands Reference** ⚡

```bash
# Run evaluation locally
python evaluation/deepeval_runner.py

# Run on specific case
python evaluation/deepeval_runner.py --case CASE-001

# Run with auto-commit
python evaluation/deepeval_runner.py --commit

# Run in Docker
docker-compose up deepeval

# View results
cat evidence/deepeval_report.json | jq .

# View git commit
git log -1 --stat
```

---

## **What's Evaluated Per Worker** 🔍

### **Medication Worker**
- ✅ Faithfulness: All changes grounded in notes?
- ✅ Hallucination: Any invented doses/drugs?
- ✅ Relevancy: Addresses all med changes?

### **Followup Worker**
- ✅ Faithfulness: Appointments grounded in needs?
- ✅ Hallucination: Any fabricated slots?
- ✅ Relevancy: Appointments address diagnoses?

### **Education Worker**
- ✅ Faithfulness: Instructions grounded in meds?
- ✅ Hallucination: Any invented symptoms?
- ✅ Relevancy: Answers patient questions?

### **Summary Worker**
- ✅ Faithfulness: Findings documented in records?
- ✅ Hallucination: Any invented clinical events?
- ✅ Relevancy: Covers key admission events?

---

## **Next Steps** 🚀

1. **Run locally first:**
   ```bash
   python evaluation/deepeval_runner.py
   ```

2. **Check results:**
   ```bash
   cat evidence/deepeval_report.json | jq .average_score
   ```

3. **Use full pipeline:**
   ```bash
   docker-compose up --build
   ```

4. **Verify git commit:**
   ```bash
   git log -1 --format="%B"
   ```

5. **Push to GitLab:**
   ```bash
   git push origin main
   # GitLab CI runs deepeval automatically
   ```

---

## **Why This Matters** 🎯

**Deterministic evaluation (you already have):**
- ✅ Fast, reproducible, no API costs
- ❌ Can't detect semantic hallucinations
- ❌ Limited to structural checks

**DeepEval evaluation (now added):**
- ✅ Detects hallucinations (LLM reasoning)
- ✅ Checks grounding (semantic validation)
- ✅ Validates relevancy (understands context)
- ✅ Committed as evidence
- ❌ Slower (10-30s per run)
- ❌ API costs (~$0.10-0.15)

**Combined = Comprehensive Quality Assurance** 🏥✅

---

**You're ready to use DeepEval with Gemini 1.5 Pro!** 🚀
