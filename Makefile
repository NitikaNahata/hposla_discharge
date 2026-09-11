# Discharge Planning & Follow-up Copilot
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: help install test run demo evidence compare metrics phoenix ui clean

help:
	@echo "  make install    create .venv and install dependencies"
	@echo "  make test       offline AC test suite (no API key needed)"
	@echo "  make run        full pipeline: tests, all cases, evidence  (./run.sh)"
	@echo "  make demo       one sample case end to end"
	@echo "  make evidence   regenerate committed evidence artifacts"
	@echo "  make compare    single-agent vs multi-agent comparison (NFR-06)"
	@echo "  make metrics    aggregate operational metrics across traces"
	@echo "  make phoenix    how to enable optional Phoenix tracing"
	@echo "  make ui         Streamlit routing + memory viewer"
	@echo "  make clean      remove runtime state (keeps committed evidence)"

install:
	python3.12 -m venv .venv || python3 -m venv .venv
	.venv/bin/python -m pip install -q --upgrade pip
	.venv/bin/python -m pip install -q -r requirements.txt
	.venv/bin/python -m pip install -q -e .
	@echo "Installed. Next:  cp .env.example .env  and add your Gemini API key."

test:
	$(PY) -m pytest -v -m "not live"

run:
	./run.sh

demo:
	$(PY) -m discharge_copilot run --case data/samples/case_001.json

evidence:
	$(PY) scripts/generate_evidence.py

compare:
	$(PY) scripts/compare_single_vs_multi.py

metrics:
	$(PY) scripts/metrics.py --write-evidence

phoenix:
	@echo "Install:  pip install -e \".[phoenix]\""
	@echo "Serve:    python -m phoenix.server.main serve   # http://localhost:6006"
	@echo "Enable:   export DISCHARGE_PHOENIX_ENABLED=1"

ui:
	$(PY) -m streamlit run app/streamlit_app.py

clean:
	rm -rf .state .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "Runtime state cleared. evidence/ is untouched — it is committed evidence."
