@echo off
REM Start QUANTX on Windows. Needs Python 3.11+ and Node.js 20+.
cd /d "%~dp0"
if not exist backend\.env copy backend\.env.example backend\.env
if not exist backend\.venv python -m venv backend\.venv
backend\.venv\Scripts\pip install -q -r backend\requirements.txt
cd web
if not exist node_modules call npm install --no-audit --no-fund
call npm run build
cd ..
start "QUANTX API" /D backend .venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
echo QUANTX is running: http://localhost:3000
cd web
call npx next start -p 3000 -H 127.0.0.1
