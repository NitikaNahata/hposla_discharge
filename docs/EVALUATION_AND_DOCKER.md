# Agent Evaluation & Docker CI/CD Guide

**This document covers instructor-required features:**
1. Agent evaluation (tool usage, answer quality, alignment)
2. Docker-based CI/CD with Rancher Desktop
3. Local testing workflow

---

## **Part 1: Agent Evaluation** 🧪

### **Why Evaluate Agents?**

The capstone rubric scores the system architecture. But instructor guidance asks: **Does the agent actually work well?**
- Is it calling the right tools?
- Are answers grounded in source data?
- Are clinically dangerous outputs caught?

---

### **What Gets Evaluated**

**1. Tool Usage** ✅
- Did medication worker call `medication_interaction_check`?
- Did followup worker call `schedule_followup`?
- Score: 0-1 (0 = wrong tools, 1 = all correct tools called)

**2. Answer Quality** ✅
- Medication: All pre-admission meds accounted? Interactions flagged?
- Followup: Risk-tier appropriate appointments scheduled?
- Education: Red-flag symptoms included (CRITICAL for safety)?
- Summary: Grounded in source data?

**3. Clinical Alignment** ✅
- Tool selection contextually appropriate?
- Output decisions consistent with patient risk?
- Escalations triggered when needed?

---

### **Run Evaluation Locally**

```bash
# Run on all committed cases
python evaluation/run_evaluation.py

# Run on specific case
python evaluation/run_evaluation.py --case CASE-001

# Output as JSON (for parsing)
python evaluation/run_evaluation.py --json

# Write report to evidence
python evaluation/run_evaluation.py --write-evidence
```

**Output Example:**
```
======================================================================
Case: CASE-001
Overall Score: 0.87 (B)
======================================================================

  MEDICATION:
    Tool usage: 0.95
    Answer quality: 0.90
    Alignment: 0.85

  FOLLOWUP:
    Tool usage: 1.0
    Answer quality: 0.88
    Alignment: 0.80
    ⚠️  HIGH risk but no appointment within 7 days

  EDUCATION:
    Tool usage: 1.0
    Answer quality: 0.92
    Alignment: 0.88

  SUMMARY:
    Tool usage: 0.90
    Answer quality: 0.85
    Alignment: 0.88

======================================================================
SUMMARY: 4 cases evaluated
Average score: 0.86
======================================================================
```

---

### **Evaluation Grades**

| Score | Grade | Meaning |
|---|---|---|
| 0.90+ | A | Excellent agent performance |
| 0.80-0.89 | B | Good, minor issues |
| 0.70-0.79 | C | Acceptable, some defects |
| <0.70 | D | Poor, serious issues |

---

### **Fix Issues Found**

If evaluation finds:
- ❌ "Medication worker didn't call interaction check"
  → Agent context missing tool prompting, or tool not in allowed list
  
- ❌ "Education has no red-flag symptoms"
  → Critic should have caught this (AC-12 failure)
  
- ❌ "HIGH risk but no appointment within 7 days"
  → Followup worker not respecting risk tier routing

---

## **Part 2: Docker & Rancher Desktop** 🐳

### **Why Docker?**

- **Reproducible:** Same code, same environment everywhere
- **Testable:** Run full pipeline locally before pushing
- **CI/CD Ready:** Mirrors what runs in GitLab
- **Portable:** Works on Mac, Linux, Windows

### **Install Rancher Desktop**

```bash
# macOS
brew install rancher-desktop

# Linux / Windows
# Visit: https://rancherdesktop.io/
```

Start it:
```bash
open -a "Rancher Desktop"
# Wait 2-3 minutes for startup
```

Verify Docker works:
```bash
docker --version
# Docker version 24.x.x...
```

---

### **Local Docker Workflow**

#### **Step 1: Build Image**
```bash
docker build -t discharge-copilot:latest .
```

#### **Step 2: Run Full Pipeline Locally**
```bash
# Build and run all services (quality + tests + evidence + evaluation)
docker-compose up --build

# Watch output:
# - Tests run in "tests" service
# - Evidence generated in "evidence" service
# - Logs printed to terminal
```

#### **Step 3: Test Individual Case in Docker**
```bash
docker run \
  --env-file .env \
  -v $(pwd)/data:/app/data:ro \
  discharge-copilot:latest \
  run --case data/samples/case_001.json
```

#### **Step 4: Run Evaluation in Docker**
```bash
docker run \
  -v $(pwd)/evaluation:/app/evaluation \
  -v $(pwd)/evidence:/app/evidence \
  discharge-copilot:latest \
  python evaluation/run_evaluation.py --write-evidence
```

#### **Step 5: Verify Everything Works**
```bash
# All services should complete successfully
docker-compose up --build
docker-compose down

# Check evidence was generated
ls -la evidence/
# Should show: traces/, transcripts/, agent_evaluation_report.json
```

---

### **Docker Compose Services**

**docker-compose.yml** defines 3 services:

```yaml
services:
  copilot:          # Main app (run one case)
  tests:            # Unit tests + AC tests
  evidence:         # Generate committed evidence
```

Run individually:
```bash
docker-compose up copilot        # Run case_001
docker-compose up tests          # Run pytest
docker-compose up evidence       # Generate evidence
```

---

## **Part 3: GitLab CI/CD Pipeline** 🔄

Your updated `.gitlab-ci.yml` includes:

```
1. build       → Build Docker image
2. quality     → Lint + type check
3. test        → Run pytest (Py 3.11 & 3.12)
4. evaluation  → Run agent evaluator
5. reproducibility → Clean install + secret check
```

---

### **Stages Explained**

**Stage 1: Build**
```yaml
build_docker:
  - Builds Docker image: discharge-copilot:$COMMIT_SHA
  - Pushes to GitLab Container Registry
```

**Stage 2: Quality**
```yaml
quality:
  - Runs in Python 3.12 base image
  - ruff lint + mypy type check
```

**Stage 3: Test**
```yaml
test_py311:
test_py312:
  - Pytest on multiple Python versions
  - Uploads JUnit XML results
```

**Stage 4: Evaluation** (NEW)
```yaml
evaluation:
  - Runs agent evaluator
  - Writes evaluation report to evidence/
```

**Stage 5: Reproducibility**
```yaml
reproducibility:
  - Clean install (pip from requirements.txt)
  - Secret check (no API keys committed)
```

---

### **Monitor CI/CD**

In GitLab:
1. Push to `main` or create a PR
2. Go to: **Project → CI/CD → Pipelines**
3. Watch stages run in order
4. View logs for each job
5. Download artifacts (JUnit, evaluation report)

---

## **Step-by-Step Setup** 📋

### **For Your Work Laptop**

```bash
# 1. Clone repo
git clone https://gitlab.com/virtusa/your-repo.git
cd your-repo

# 2. Install Rancher Desktop
brew install rancher-desktop
open -a "Rancher Desktop"

# 3. Copy env file
cp .env.example .env
# Edit .env, add GOOGLE_API_KEY

# 4. Build Docker image
docker build -t discharge-copilot:latest .

# 5. Run full pipeline
docker-compose up --build

# 6. View results
ls -la evidence/
cat evidence/agent_evaluation_report.json

# 7. Commit and push
git add -A
git commit -m "feat: add evaluation and Docker CI/CD"
git push origin main

# 8. Monitor in GitLab
# Go to: Project → CI/CD → Pipelines
```

---

## **Common Commands** ⚡

```bash
# Build
docker build -t discharge-copilot:latest .

# Run all services
docker-compose up --build

# Run specific service
docker-compose up tests
docker-compose up evaluation

# View logs
docker-compose logs -f copilot

# Stop all
docker-compose down

# Clean up
docker system prune -a

# Run shell inside container
docker run -it discharge-copilot:latest bash

# Check if image works
docker run discharge-copilot:latest python -m pytest --version

# View evaluation report
python evaluation/run_evaluation.py --json | jq .
```

---

## **Troubleshooting** 🔧

### **Docker build fails**
```bash
# Rebuild without cache
docker build --no-cache -t discharge-copilot:latest .

# Check Dockerfile
cat Dockerfile
```

### **Rancher Desktop won't start**
```bash
# Restart
rancher-desktop shutdown
sleep 5
rancher-desktop start
```

### **Evaluation script not found**
```bash
# Verify file exists
ls -la evaluation/run_evaluation.py

# Verify imports
python -c "from evaluation.agent_evaluator import *"
```

### **Docker network issues**
```bash
# Reset docker networking
docker-compose down
docker network prune -f
docker-compose up --build
```

---

## **Summary of Changes** 📝

| File | Purpose |
|---|---|
| `Dockerfile` | Container image definition |
| `docker-compose.yml` | Multi-service local testing |
| `.gitlab-ci.yml` | Updated with Docker build + evaluation stage |
| `evaluation/agent_evaluator.py` | Evaluation metrics & scoring |
| `evaluation/run_evaluation.py` | CLI to run evaluation |
| `docs/rancher-desktop-cicd.md` | Detailed Rancher setup guide |

---

## **Next Steps** 🚀

1. **Install Rancher Desktop** on your work laptop
2. **Run locally:** `docker-compose up --build`
3. **Verify evaluation:** `python evaluation/run_evaluation.py`
4. **Commit:** `git add -A && git commit -m "feat: evaluation + Docker CI/CD"`
5. **Push:** `git push origin main`
6. **Monitor:** GitLab CI/CD pipeline runs automatically

**You're ready to use instructor-required evaluation + Docker CI/CD!** ✅
