@echo off
setlocal
rem Double-click to open the N02 GUI without PowerShell or token flags.
rem No production activation, scheduling, or permission modifications.
set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
where python.exe >NUL 2>&1
if errorlevel 1 (
    echo Python 3.12 or newer is required. No settings were changed.
    pause
    exit /b 1
)
python.exe -m project_orchestrator.notion_n02_connection_manager
if errorlevel 1 (
    echo The connection manager could not start. Check Python and Tkinter.
    pause
    exit /b 1
)
endlocal
