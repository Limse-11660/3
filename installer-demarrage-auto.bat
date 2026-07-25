@echo off
rem Installe le demarrage automatique (sans droits admin) : raccourci dans le dossier
rem Demarrage de la session -> le veilleur se lance sans fenetre (pythonw) a chaque
rem ouverture de session Windows. Suivi via veilleur.log et Discord.
rem Desinstallation : desinstaller-demarrage-auto.bat
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$d = '%~dp0'.TrimEnd('\');" ^
 "$lnk = [Environment]::GetFolderPath('Startup') + '\veilleur-tm.lnk';" ^
 "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk);" ^
 "$s.TargetPath = \"$d\.venv\Scripts\pythonw.exe\";" ^
 "$s.Arguments = '-m veilleur run';" ^
 "$s.WorkingDirectory = $d;" ^
 "$s.Description = 'Moniteur de disponibilite Ticketmaster (veilleur)';" ^
 "$s.Save();" ^
 "Write-Host ('Raccourci installe : ' + $lnk)"
pause
