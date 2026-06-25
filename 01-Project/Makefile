# Generative Market Simulation — developer tasks.
# The project path may contain spaces, so we always call the venv python via -m.

PY := ./.venv/bin/python
PIP := ./.venv/bin/pip
FRONTEND := web

.PHONY: help setup setup-py setup-web build-web web serve dev-api dev-ui demo test clean

help:
	@echo "targets:"
	@echo "  setup      create venv, install Python + frontend deps"
	@echo "  build-web  build the browser UI into market_sim/server/static"
	@echo "  web        build the UI and serve the app (http://127.0.0.1:8000)"
	@echo "  serve      serve the app without rebuilding the UI"
	@echo "  dev-api    run the backend only (for frontend dev with hot reload)"
	@echo "  dev-ui     run the Vite dev server (proxies to dev-api)"
	@echo "  demo       run the terminal CLI demo (init, run 200, plot, replay)"
	@echo "  test       run the Python test suite"
	@echo "  clean      remove venv, node_modules, build output, runs/"

setup: setup-py setup-web

setup-py:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

setup-web:
	cd $(FRONTEND) && npm install

build-web:
	cd $(FRONTEND) && npm run build

web: build-web serve

serve:
	$(PY) -m market_sim.server

dev-api:
	$(PY) -m market_sim.server

dev-ui:
	cd $(FRONTEND) && npm run dev

demo:
	$(PY) -m market_sim.cli.main init --config scenarios/demo.yaml
	$(PY) -m market_sim.cli.main run --rounds 200
	$(PY) -m market_sim.cli.main status
	$(PY) -m market_sim.cli.main replay --log runs/demo.jsonl
	$(PY) -m market_sim.cli.main plot --log runs/demo.jsonl --out runs/demo.png
	@echo "demo figure -> runs/demo.png"

test:
	$(PY) -m pytest

clean:
	rm -rf .venv $(FRONTEND)/node_modules market_sim/server/static runs *.session.pkl
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
