@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m accu_importer
if errorlevel 1 pause
