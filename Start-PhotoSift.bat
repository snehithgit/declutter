@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Run Setup-Windows.bat first.
 exit /b 1
)
.venv\Scripts\python.exe -m photosift run --config config.toml
