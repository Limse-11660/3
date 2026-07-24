@echo off
rem Surveillance continue — fermer la fenetre ou Ctrl+C pour arreter.
cd /d "%~dp0"
.venv\Scripts\python.exe -m veilleur run
pause
