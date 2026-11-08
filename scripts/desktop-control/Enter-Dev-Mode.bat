@echo off
rem Closes the Dev Mode list of memory hogs (politely) so the engine can develop. Reversible: run Exit-Dev-Mode.bat.
cd /d "%~dp0\..\.."
python scripts\iron_god_dev_mode.py plan
echo.
echo About to close the programs marked CLOSE above. Press Ctrl+C to cancel, or
pause
python scripts\iron_god_dev_mode.py enter --yes
echo.
pause
