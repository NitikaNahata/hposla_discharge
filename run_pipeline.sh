#!/bin/bash

# Discharge Planning Copilot — Full Pipeline (No Docker Required)
#
# This script runs the complete pipeline with deterministic checks + DeepEval
# WITHOUT requiring Docker. Perfect if you don't have Docker/Rancher Desktop.
#
# Usage:
#   chmod +x run_pipeline.sh
#   ./run_pipeline.sh
#
# What it does:
#   1. Install dependencies (pip install -r requirements.txt)
#   2. Quality checks (ruff + mypy)
#   3. Run tests (pytest)
#   4. Generate evidence
#   5. Run deterministic evaluation
#   6. Run DeepEval (Gemini 1.5 Pro)
#   7. Auto-commit results to git
#
# Time: ~3-5 minutes
# Cost: ~$0.10-0.15 (Gemini API calls)

set -e  # Exit on error

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  Discharge Planning Copilot — Full Pipeline                ║"
echo "║  (No Docker Required)                                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✅ Python version: $PYTHON_VERSION"
echo ""

# Step 1: Install dependencies
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 1: Installing dependencies..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "✅ Dependencies installed"
echo ""

# Step 2: Check .env file
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 2: Environment setup..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ ! -f .env ]; then
    echo "⚠️  .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "❌ MANUAL STEP REQUIRED:"
    echo "   Edit .env and add your GOOGLE_API_KEY"
    echo "   Then run this script again"
    exit 1
fi

if ! grep -q "GOOGLE_API_KEY=" .env || grep "GOOGLE_API_KEY=$" .env; then
    echo "❌ ERROR: GOOGLE_API_KEY not set in .env"
    echo "   Edit .env and add your GOOGLE_API_KEY"
    exit 1
fi
echo "✅ .env configured (GOOGLE_API_KEY found)"
echo ""

# Step 3: Quality checks
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 3: Quality checks (ruff + mypy)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  📋 Linting with ruff..."
ruff check . > /dev/null 2>&1 && echo "  ✅ Lint passed" || echo "  ⚠️  Lint issues found"

echo "  📋 Type checking with mypy..."
mypy > /dev/null 2>&1 && echo "  ✅ Type check passed" || echo "  ⚠️  Type issues found"
echo ""

# Step 4: Run tests
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 4: Running tests (pytest)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
pytest -v -m "not live" --junitxml=junit.xml 2>&1 | tail -20
echo "✅ Tests completed"
echo ""

# Step 5: Run all 4 cases
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 5: Running all 4 cases..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
for case in case_001 case_002 case_003 case_004; do
  echo "  Running $case..."
  python -m discharge_copilot run --case data/samples/${case}.json > /dev/null 2>&1
  echo "  ✅ $case complete"
done
echo "✅ All 4 cases complete"
echo ""

# Step 6: Deterministic evaluation
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 6: Running deterministic evaluation..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python evaluation/run_evaluation.py --write-evidence
echo "✅ Deterministic evaluation complete"
echo ""

# Step 7: DeepEval (Gemini 1.5 Pro)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "STEP 7: Running DeepEval (Gemini 1.5 Pro)..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
python evaluation/deepeval_runner.py --commit
echo "✅ DeepEval complete (results committed)"
echo ""

# Summary
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  ✅ PIPELINE COMPLETE                                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "📁 Results saved to:"
echo "   ✅ evidence/traces/*.jsonl"
echo "   ✅ evidence/transcripts/*.md"
echo "   ✅ evidence/agent_evaluation_report.json"
echo "   ✅ evidence/deepeval_report.json"
echo ""
echo "📝 Git commit:"
git log -1 --oneline
echo ""
echo "🚀 Next: Push to GitLab"
echo "   git push origin main"
echo ""
