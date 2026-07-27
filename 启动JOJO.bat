@echo off
title JOJO Director Launcher
echo Starting JOJO Director ...

start "JOJO-backend-8000" cmd /k "cd /d "%~dp0backend" && python -m uvicorn app.main:app --reload --port 8000"
start "JOJO-frontend-5173" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo Waiting 8 seconds, then opening browser ...
timeout /t 8 /nobreak >nul
start "" http://localhost:5173

echo.
echo Done. Keep the two black windows open while using JOJO Studio.
echo Close them to stop the services.
pause
