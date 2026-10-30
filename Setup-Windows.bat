@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe py -3.12 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -e ".[dev]"
if errorlevel 1 exit /b 1
if not exist config.toml copy config.example.toml config.toml >nul
cd frontend
call npm ci --no-audit --no-fund
if errorlevel 1 exit /b 1
call npm run build
if errorlevel 1 exit /b 1
echo Setup complete. Run Start-PhotoSift.bat.
