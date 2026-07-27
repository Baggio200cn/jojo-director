@echo off
title Install JOJO Director AutoStart
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\JOJO-Director.lnk'); $s.TargetPath='%~dp0启动JOJO-静默.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()"
echo.
echo Done. JOJO Director services will start automatically after Windows boots.
echo Bookmark:  http://localhost:5173
echo To remove: double-click Uninstall bat in the same folder.
pause
