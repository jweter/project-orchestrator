@echo off
rem Shows what Dev Mode WOULD close and what is protected. Changes nothing.
cd /d "%~dp0\..\.."
python scripts\iron_god_dev_mode.py plan
echo.
pause
