@echo off
REM Starts the FastAPI backend and the React dev server in two windows.
cd /d "%~dp0.."
if not exist backend\.venv ( echo Run scripts\setup.bat first. & exit /b 1 )

start "WP Image Automation - backend" cmd /k "cd /d %cd%\backend && call .venv\Scripts\activate.bat && python run.py"
start "WP Image Automation - frontend" cmd /k "cd /d %cd%\frontend && npm run dev"
timeout /t 5 >nul
start http://localhost:5173
