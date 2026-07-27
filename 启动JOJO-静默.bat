@echo off
rem Silent start: services run minimized, no browser popup. Used by auto-start.
start "JOJO-backend-8000" /min cmd /c "cd /d "%~dp0backend" && python -m uvicorn app.main:app --port 8000"
start "JOJO-frontend-5173" /min cmd /c "cd /d "%~dp0frontend" && npm run dev"
