@echo off
REM The Gallery: one-click local run on Windows (needs Python 3.10+ from python.org)
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
call ".venv\Scripts\activate.bat"

echo Installing requirements (first run takes a minute)...
python -m pip install -q --disable-pip-version-check -r requirements.txt
findstr /r /c:"^MODEL_PROVIDER=foundry_local" .env >nul 2>&1 && (
  echo Installing Microsoft Foundry Local SDK...
  python -m pip install -q --disable-pip-version-check -r requirements-foundry.txt
)

echo.
echo  The Gallery is starting at http://localhost:8000
echo  Press Ctrl+C in this window to stop it.
echo.
start "" cmd /c "timeout /t 4 >nul & start http://localhost:8000"

cd backend
python -m uvicorn gallery.app:app --port 8000
