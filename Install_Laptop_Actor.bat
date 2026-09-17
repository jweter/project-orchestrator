@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_laptop_actor.ps1"
if errorlevel 1 (
  echo.
  echo LAPTOP ACTOR INSTALL FAILED
  pause
  exit /b 1
)
echo.
echo ============================================================
echo   YOU ARE OUT OF THE ROUTINE ENGINEERING TEST LOOP
echo ============================================================
echo.
echo The actor now self-updates, waits for Windows idle time,
echo claims bounded GitHub work packets, and returns sanitized evidence.
pause
exit /b 0
