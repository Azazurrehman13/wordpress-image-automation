@echo off
REM One-time setup for Windows. Requires Python 3.11+ and Node.js 18+.
cd /d "%~dp0.."

if not exist backend\.venv (
  py -3.11 -m venv backend\.venv || py -3 -m venv backend\.venv || goto :err
)
call backend\.venv\Scripts\activate.bat || goto :err
python -m pip install --upgrade pip
pip install -r backend\requirements.txt || goto :err
playwright install chromium || goto :err

pushd frontend
call npm install || goto :err
popd

if not exist backend\.env copy backend\.env.example backend\.env >nul
echo.
echo Setup complete. Open backend\.env and set ANTHROPIC_API_KEY, then run scripts\start.bat
exit /b 0

:err
echo.
echo Setup failed. See the messages above.
exit /b 1
