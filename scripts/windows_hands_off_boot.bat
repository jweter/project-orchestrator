@echo off
setlocal
set "REPO=C:\Users\jerem\Documents\GitHub\project-orchestrator"
set "WORKSPACE=C:\Users\jerem\Documents\GitHub"
set "STATE=%REPO%\.hands-off-state"
cd /d "%REPO%"
if not exist "%STATE%" mkdir "%STATE%"
echo [%DATE% %TIME%] boot wrapper invoked>>"%STATE%\windows-boot-wrapper.log"
python "%REPO%\scripts\launch_persistent_supervisor.py" --repo-root "%REPO%" --workspace-root "%WORKSPACE%" --state-root "%STATE%" --timeout-seconds 60 --builder-seconds 60 --merge-seconds 60 >>"%STATE%\windows-boot-wrapper.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] launcher exit %RC%>>"%STATE%\windows-boot-wrapper.log"
exit /b %RC%
