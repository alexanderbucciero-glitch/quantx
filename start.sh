#!/usr/bin/env bash
# Start QUANTX on macOS / Linux. Needs Python 3.11+ and Node.js 20+.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f backend/.env ]; then cp backend/.env.example backend/.env; echo "Created backend/.env — add your email to QUANTX_USER_AGENT."; fi
if [ ! -d backend/.venv ]; then python3 -m venv backend/.venv; fi
backend/.venv/bin/pip install -q -r backend/requirements.txt
( cd web && [ -d node_modules ] || npm install --no-audit --no-fund )
( cd web && npm run build )

( cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 ) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT
echo
echo "QUANTX is running:  http://localhost:3000   (Ctrl+C to stop)"
cd web && npx next start -p 3000 -H 127.0.0.1
