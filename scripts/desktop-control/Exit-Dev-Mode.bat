@echo off
rem Relaunches what Dev Mode closed (browsers reopen their last session).
cd /d "%~dp0\..\.."
python scripts\iron_god_dev_mode.py restore
echo.
pause
