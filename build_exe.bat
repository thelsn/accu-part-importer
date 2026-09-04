@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean AccuPartImporter.spec
if errorlevel 1 pause
