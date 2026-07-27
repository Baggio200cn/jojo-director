@echo off
title Remove JOJO Director AutoStart
powershell -NoProfile -Command "Remove-Item ([Environment]::GetFolderPath('Startup')+'\JOJO-Director.lnk') -ErrorAction SilentlyContinue"
echo AutoStart removed.
pause
