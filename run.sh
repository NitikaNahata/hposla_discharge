#!/usr/bin/env bash
#
# Discharge Planning & Follow-up Copilot — the single documented command (NFR-02).
#
#   ./run.sh              full: tests, all four sample cases, evidence regeneration
#   ./run.sh --quick      one sample case only
#   ./run.sh --tests      offline test suite only (no API key needed)
#   ./run.sh --evidence   regenerate committed evidence only
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
RED=$'\033[31m'; RESET=$'\033[0m'

step()  { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
info()  { printf '    %s%s%s\n' "$DIM" "$1" "$RESET"; }
ok()    { printf '    %s✓ %s%s\n' "$GREEN" "$1" "$RESET"; }
warn()  { printf '    %s! %s%s\n' "$YELLOW" "$1" "$RESET"; }
fail()  { printf '    %s✗ %s%s\n' "$RED" "$1" "$RESET"; }

MODE="${1:-full}"

# --- Python ------------------------------------------------------------------
if [[ -x ".venv/bin/python" ]]; then
    PY=".venv/bin/python"
    info "using .venv"
else
    PY="$(command -v python3 || command -v python)"
    warn "no .venv found — using $PY"
    warn "recommended:  python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"
fi

"$PY" - <<'EOF'
import sys
if sys.version_info < (3, 11):
    sys.exit(f"Python 3.11+ required; found {sys.version.split()[0]}")
EOF

# --- Dependencies ------------------------------------------------------------
if ! "$PY" -c "import langgraph, chromadb, mcp" >/dev/null 2>&1; then
    step "Installing dependencies"
    "$PY" -m pip install -q -r requirements.txt
    "$PY" -m pip install -q -e .
    ok "dependencies installed"
fi

# --- Configuration -----------------------------------------------------------
HAVE_KEY=0
if [[ -f .env ]] && grep -qE '^GOOGLE_API_KEY=.+' .env \
   && ! grep -qE '^GOOGLE_API_KEY=your-gemini-api-key-here' .env; then
    HAVE_KEY=1
elif [[ -n "${GOOGLE_API_KEY:-}" ]]; then
    HAVE_KEY=1
fi

if [[ "$HAVE_KEY" -eq 0 ]]; then
    warn "GOOGLE_API_KEY is not configured."
    warn "  cp .env.example .env   then add your key from https://aistudio.google.com/apikey"
    warn "Running the offline portions only."
fi

# --- Tests -------------------------------------------------------------------
if [[ "$MODE" == "full" || "$MODE" == "--tests" ]]; then
    step "Test suite — AC traceability (offline, no API key required)"
    if "$PY" -m pytest -q -m "not live" 2>&1 | tail -5; then
        ok "tests passed"
    else
        fail "tests failed"
        exit 1
    fi
    [[ "$MODE" == "--tests" ]] && exit 0
fi

# --- Knowledge index ---------------------------------------------------------
step "Building the agentic-RAG index"
"$PY" - <<'EOF'
import sys; sys.path.insert(0, "src")
from discharge_copilot.tools.rag import ClinicalGuidanceIndex
print("   ", ClinicalGuidanceIndex().build())
EOF
ok "index ready"

if [[ "$HAVE_KEY" -eq 0 ]]; then
    step "Regenerating offline evidence"
    "$PY" scripts/generate_evidence.py --offline
    ok "offline evidence written to evidence/"
    printf '\n%sAdd your Gemini API key to .env and re-run for the full pipeline.%s\n' \
        "$YELLOW" "$RESET"
    exit 0
fi

# --- Evidence only -----------------------------------------------------------
if [[ "$MODE" == "--evidence" ]]; then
    step "Regenerating all committed evidence"
    "$PY" scripts/generate_evidence.py
    exit 0
fi

# --- Quick ------------------------------------------------------------------
if [[ "$MODE" == "--quick" ]]; then
    step "Running one discharge case"
    "$PY" -m discharge_copilot run --case data/samples/case_001.json --quiet
    exit 0
fi

# --- Full --------------------------------------------------------------------
step "Running all committed discharge cases"
for case_file in data/samples/case_*.json; do
    info "$(basename "$case_file")"
    "$PY" -m discharge_copilot run --case "$case_file" --quiet
done
ok "all cases completed"

step "Regenerating committed evidence"
"$PY" scripts/generate_evidence.py

step "Aggregating operational metrics"
"$PY" scripts/metrics.py --write-evidence >/dev/null
ok "evidence/logs/observability.log"

step "Done"
cat <<'SUMMARY'

    Evidence:  evidence/transcripts/  evidence/traces/  evidence/logs/
    AC map:    docs/acceptance-criteria.md
    Index:     docs/evidence-index.md

    Other entry points:
      python -m discharge_copilot run --case data/samples/case_003.json --pause-after medication
      python -m discharge_copilot resume --case-id CASE-003
      python -m discharge_copilot memory --patient MRN-2001
      python scripts/metrics.py
      python scripts/compare_single_vs_multi.py
      streamlit run app/streamlit_app.py

SUMMARY
