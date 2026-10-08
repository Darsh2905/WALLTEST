# WALLTEST. `make demo` is the one command. Windows without make: python scripts/demo.py
PY ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
VENV_PY = .venv/bin/python

.PHONY: demo down reset venv dev test test-fast e2e calibrate defaults screenshots frontend-test clean

demo:            ## build + start Postgres 16/pgvector + API + UI (migrations and seed run automatically), open the browser
	$(PY) scripts/demo.py

down:            ## stop the stack
	$(PY) scripts/demo.py --down

reset:           ## stop and delete the database volume, then start fresh
	$(PY) scripts/demo.py --reset

venv:            ## Python virtualenv with the dev/test dependencies
	python3 -m venv .venv && $(VENV_PY) -m pip install -q -r backend/requirements-dev.txt && $(VENV_PY) -m playwright install chromium

dev:             ## development mode: DB in docker, API with reload on :8000, Vite on :5173
	docker compose up -d db
	$(VENV_PY) -m walltest.bootstrap 2>/dev/null || (cd backend && ../$(VENV_PY) -m walltest.bootstrap)
	cd backend && ../$(VENV_PY) -m uvicorn walltest.api:app --reload --port 8000 & cd frontend && npm run dev

test:            ## everything except the browser tests: pytest against real PostgreSQL (needs `docker compose up -d db`)
	docker compose up -d db
	cd backend && ../$(VENV_PY) -m pytest -q

e2e:             ## Playwright end-to-end (drives a LIVE campaign through the UI; ~3 minutes)
	cd frontend && npm run build
	cd backend && ../$(VENV_PY) -m pytest -q -m e2e tests/e2e

frontend-test:
	cd frontend && npm test

calibrate:       ## 2,000+ null campaigns through the real engine (~10 minutes)
	$(VENV_PY) scripts/calibrate.py

defaults:        ## re-derive the demo defaults from the exact power calculation
	$(VENV_PY) scripts/choose_defaults.py

screenshots:     ## all pages at 1440x900 and 1366x768 into docs/screenshots (stack must be running)
	$(VENV_PY) scripts/screenshots.py --theme light && $(VENV_PY) scripts/screenshots.py --theme dark
