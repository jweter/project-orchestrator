@echo off
setlocal
set "REPO=C:\Users\jerem\Documents\GitHub\project-orchestrator"
cd /d "%REPO%"
if not exist ".hands-off-state" mkdir ".hands-off-state"
echo [%DATE% %TIME%] boot wrapper invoked>>".hands-off-state\windows-boot-wrapper.log"
python scripts\launch_persistent_supervisor.py --repo-root "%REPO%" --workspace-root "C:\Users\jerem\Documents\GitHub" --state-root "%REPO%\.hands-off-state" --timeout-seconds 60 --builder-seconds 60 --merge-seconds 60 >>".hands-off-state\windows-boot-wrapper.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] launcher exit %RC%>>".hands-off-state\windows-boot-wrapper.log"
exit /b %RC%
