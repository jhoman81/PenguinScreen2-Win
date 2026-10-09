@echo off
rem Start PenguinScreen2 in VR. See PenguinScreen2-VR.ps1 for options (-Check reports the OpenXR runtime).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0PenguinScreen2-VR.ps1" %*
if errorlevel 1 pause
