#!/usr/bin/env bash
# Start NODAL on http://127.0.0.1:8000 (needs ./.venv and the organiser CSVs; see README)
cd "$(dirname "$0")/backend" && exec ../.venv/bin/python -m uvicorn nodal.api:app --host 127.0.0.1 --port 8000
