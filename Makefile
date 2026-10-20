VENV := server/.venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: setup test run mcp seed

setup:
	python3 -m venv $(VENV)
	$(PIP) install -q -r server/requirements.txt -r server/requirements-dev.txt

test:
	cd server && ../$(PY) -m pytest tests/ -q

run:
	cd server && ../$(PY) -m onrecord.ingest

mcp:
	cd server && ../$(PY) -m onrecord.mcp_server

seed:
	cd server && ONRECORD_REHEARSAL=1 ../$(PY) -m onrecord.seed
