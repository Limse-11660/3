@echo off
rem Supprime le demarrage automatique et arrete l'instance invisible du veilleur.
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\veilleur-tm.lnk" 2>nul
powershell -NoProfile -Command ^
 "Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*monitor-ticketmaster*' } | Stop-Process -Force;" ^
 "Write-Host 'Demarrage automatique desinstalle, instance invisible arretee (le cas echeant).'"
pause
