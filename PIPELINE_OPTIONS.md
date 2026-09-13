# Run the Pipeline — Two Options

Choose ONE option based on what you have installed:

---

## **OPTION 1: WITH Docker** 🐳

**Requires:** Docker + Rancher Desktop

**Command:**
```bash
docker-compose up --build
```

**That's it!** One command runs everything.

**Time:** ~3-5 minutes

**What it does:**
- Builds Docker image
- Runs app (case_001)
- Runs tests
- Generates evidence
- Runs DeepEval (Gemini 1.5 Pro)
- Auto-commits results to git

**Setup (one time):**
```bash
# 1. Install Rancher Desktop
brew install rancher-desktop

# 2. Start it
open -a "Rancher Desktop"

# 3. Wait 2-3 minutes for startup

# 4. Verify Docker works
docker --version
# Should show: Docker version 24.x.x...
```

**Then run:**
```bash
docker-compose up --build
```

---

## **OPTION 2: WITHOUT Docker** 🐍

**Requires:** Python 3.12+

**Setup (one time):**
```bash
# 1. Make script executable
chmod +x run_pipeline.sh

# 2. Copy and edit .env
cp .env.example .env
# Edit .env, add your GOOGLE_API_KEY
```

**Command:**
```bash
./run_pipeline.sh
```

**That's it!** One script runs everything.

**Time:** ~3-5 minutes

**What it does:**
1. Installs dependencies (pip install -r requirements.txt)
2. Quality checks (ruff + mypy)
3. Runs tests (pytest)
4. Generates evidence
5. Runs deterministic evaluation
6. Runs DeepEval (Gemini 1.5 Pro)
7. Auto-commits results to git

---

## **Quick Comparison** 📊

| | With Docker | Without Docker |
|---|---|---|
| **Command** | `docker-compose up --build` | `./run_pipeline.sh` |
| **Prerequisites** | Docker + Rancher | Python 3.12+ |
| **Setup time** | 5 min | 1 min |
| **Run time** | 3-5 min | 3-5 min |
| **One command?** | ✅ YES | ✅ YES |
| **Reproducibility** | ✅ Perfect | ⚠️ Good |

---

## **Which to Choose?** 🤔

**Use Docker IF:**
- ✅ You have Rancher Desktop installed
- ✅ You want guaranteed reproducibility
- ✅ You're in CI/CD (GitLab runs Docker)
- ✅ You want exact same env as production

**Use Python Script IF:**
- ✅ You don't want to install Docker
- ✅ You want quick local testing
- ✅ You're just developing locally
- ✅ You want to see output in real-time

---

## **Both Produce Same Results** ✅

Either option produces:
```
evidence/
├── traces/
│   ├── case_001.jsonl
│   ├── case_002.jsonl
│   └── ...
├── transcripts/
│   └── *.md
├── agent_evaluation_report.json      (deterministic)
└── deepeval_report.json              (LLM-based, Gemini 1.5 Pro)
```

And commits to git:
```
git log -1
# Shows:
# docs: add DeepEval assessment report (Gemini 1.5 Pro)
#
# evidence/deepeval_report.json (newly added)
```

---

## **Example Runs** 🚀

### **With Docker:**
```bash
$ docker-compose up --build

[+] Building 45.3s (15/15) FINISHED
[+] Running 4/4
 ✓ copilot started
 ✓ tests started
 ✓ evidence started
 ✓ deepeval started

deepeval   | Running DeepEval LLM-based assessment (Gemini 1.5 Pro)...
deepeval   | 📋 Evaluating CASE-001...
deepeval   | ✅ CASE-001: Score 0.88
...
deepeval   | ✅ Results committed to git!

✓ All services completed successfully
```

### **Without Docker:**
```bash
$ ./run_pipeline.sh

╔════════════════════════════════════════════════════════════╗
║  Discharge Planning Copilot — Full Pipeline                ║
║  (No Docker Required)                                       ║
╚════════════════════════════════════════════════════════════╝

STEP 1: Installing dependencies...
✅ Dependencies installed

STEP 2: Environment setup...
✅ .env configured (GOOGLE_API_KEY found)

STEP 3: Quality checks (ruff + mypy)...
  ✅ Lint passed
  ✅ Type check passed

STEP 4: Running tests (pytest)...
✅ Tests completed

STEP 5: Generating evidence...
✅ Evidence generated

STEP 6: Running deterministic evaluation...
✅ Deterministic evaluation complete

STEP 7: Running DeepEval (Gemini 1.5 Pro)...
✅ DeepEval complete (results committed)

╔════════════════════════════════════════════════════════════╗
║  ✅ PIPELINE COMPLETE                                       ║
╚════════════════════════════════════════════════════════════╝
```

---

## **The REAL Difference** 🎯

**Docker version:** Runs in isolated containers (exact reproducibility)

**Python script version:** Runs on your system Python (simpler setup)

**Both:** Use same Gemini 1.5 Pro, commit same results, produce same evidence

---

## **Troubleshooting** 🔧

### **Docker version fails:**
```bash
# Check Docker is running
docker ps

# Restart Rancher
rancher-desktop shutdown
sleep 5
rancher-desktop start

# Try again
docker-compose up --build
```

### **Python script fails:**
```bash
# Check Python version
python3 --version
# Should be 3.12+

# Check .env has API key
grep GOOGLE_API_KEY .env

# Try again
./run_pipeline.sh
```

---

## **Summary** ✅

**Pick ONE:**

```bash
# Option 1: Have Docker? Use this
docker-compose up --build

# Option 2: No Docker? Use this
./run_pipeline.sh
```

**Both are "one and done"** — run ONE command and everything happens automatically! 🎉

---

## **Next: Push to GitLab** 🚀

After either command completes:

```bash
git push origin main
```

GitLab CI/CD runs automatically (uses Docker regardless)

Done! 🎉
