@echo off
rem Une seule passe de verification (baselines + compteurs), puis pause.
cd /d "%~dp0"
.venv\Scripts\python.exe -m veilleur check-once
pause
