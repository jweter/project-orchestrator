param(
  [string]$ActorRepo=(Join-Path $env:LOCALAPPDATA "ExponentialEngine\actor-repo"),
  [string]$StateRoot=(Join-Path $env:LOCALAPPDATA "ExponentialEngine\flow-state"),
  [string]$ControlRepo=(Join-Path $env:LOCALAPPDATA "ExponentialEngine\FallbackControl\control-repo")
)
$ErrorActionPreference="Stop"
$root=Join-Path $env:LOCALAPPDATA "ExponentialEngine\FallbackControl"
$state=Join-Path $root "state.json"
$progress=Join-Path $root "progress.json"
$history=Join-Path $root "history.json"
$humanMode=Join-Path $StateRoot "human-mode.json"
$allowed=@("NO_OP","STATUS_SNAPSHOT","SYNC_ACTOR_MAIN","KICK_SUPERVISOR","RESTART_SUPERVISOR","DEPLOY_MAIN_SAFE","RESTART_REMOTE_CONTROL","RECOVER_CONTROL_PLANE","REBOOT_WINDOWS","HUMAN_MODE","FULL_POWER")

function Get-ActiveProductWorkers {
  @(Get-CimInstance Win32_Process|Where-Object{
    $_.Name -like "python*" -and $_.CommandLine -like "*autonomous_slice_builder.py*"
  })
}

function Write-Progress($commandId,$action,$status){
  @{command_id=$commandId;action=$action;status=$status;at=[DateTimeOffset]::UtcNow.ToString("o")} |
    ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $progress
}

function Append-History($o){
  $payload=@{schema_version=1;commands=@()}
  if(Test-Path $history){
    try{$payload=Get-Content $history -Raw|ConvertFrom-Json}catch{}
  }
  $commands=@($payload.commands)
  $last=$commands|Select-Object -Last 1
  if(-not ($last -and $last.command_id -eq $o.command_id -and $last.status -eq $o.status -and $last.action -eq $o.action)){
    $commands += $o
  }
  @{schema_version=1;commands=@($commands|Select-Object -Last 20)} |
    ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $history
}

function Test-HumanMode {
  Test-Path $humanMode
}

function Get-AutonomousProcesses {
  @(Get-CimInstance Win32_Process|Where-Object{
    $_.Name -like "python*" -and (
      $_.CommandLine -like "*continuous_flow_supervisor.py*" -or
      $_.CommandLine -like "*green_pr_watch_loop.py*" -or
      $_.CommandLine -like "*autonomous_slice_builder.py*" -or
      $_.CommandLine -like "*laptop_actor_bootstrap.py*"
    )
  })
}

function Stop-AutonomousProcesses {
  foreach($p in @(Get-AutonomousProcesses)){
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
}

function Set-WorkerTasksEnabled([bool]$Enabled){
  foreach($taskName in @(
    "ExponentialEngine-ContinuousFlow-Watchdog",
    "ExponentialEngine-LaptopActor",
    "Portfolio Idle Worker",
    "Rocksmith CDLC Unattended Worker",
    "Everward Unattended Product Reality",
    "Azathoth-Idle-App-Watcher",
    "Azathoth-Idle-Mine-Watcher"
  )){
    $task=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if(-not $task){continue}
    if($Enabled){
      Enable-ScheduledTask -TaskName $taskName|Out-Null
      Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    } else {
      Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
      Disable-ScheduledTask -TaskName $taskName|Out-Null
    }
  }
}

function Publish($o,[bool]$Consume=$true){
  $json=$o|ConvertTo-Json -Depth 8
  if($Consume){$json|Set-Content -Encoding UTF8 $state}
  Write-Progress $o.command_id $o.action $o.status
  Append-History $o
  $receiptPath=Join-Path $ControlRepo "control\receipt.json"
  $publishRemote=$true
  if(Test-Path $receiptPath){
    try{
      $existing=Get-Content $receiptPath -Raw|ConvertFrom-Json
      if($existing.command_id -eq $o.command_id -and
         $existing.status -eq $o.status -and
         $existing.action -eq $o.action){
        $publishRemote=$false
      }
    }catch{}
  }
  if($publishRemote){
    $json|Set-Content -Encoding UTF8 $receiptPath
    git -C $ControlRepo add control/receipt.json
    git -C $ControlRepo diff --cached --quiet
    if($LASTEXITCODE -ne 0){
      git -C $ControlRepo commit -m ("control: receipt "+$o.command_id) --quiet 2>$null
      git -C $ControlRepo push origin azathoth-control --quiet
    }
  }
}

function Test-ActorRepoClean {
  $dirty=@(git -C $ActorRepo status --porcelain)
  if($LASTEXITCODE -ne 0){return $false}
  return $dirty.Count -eq 0
}

function Invoke-SafeActorSync {
  $workers=@(Get-ActiveProductWorkers)
  if($workers.Count -gt 0){
    return @{status="DEFERRED_ACTIVE_PRODUCT_WORKERS";consume=$false;worker_count=$workers.Count}
  }
  if(-not (Test-ActorRepoClean)){
    return @{status="REJECTED_ACTOR_REPO_DIRTY";consume=$true;worker_count=0}
  }
  git -C $ActorRepo fetch origin main --quiet
  if($LASTEXITCODE -ne 0){
    return @{status="SYNC_FETCH_FAILED";consume=$true;worker_count=0}
  }
  $uniqueCommits=@(git -C $ActorRepo rev-list origin/main..HEAD)
  if($LASTEXITCODE -ne 0){
    return @{status="SYNC_HISTORY_CHECK_FAILED";consume=$true;worker_count=0}
  }
  if($uniqueCommits.Count -gt 0){
    return @{status="REJECTED_ACTOR_UNPUSHED_COMMITS";consume=$true;worker_count=0}
  }
  $workers=@(Get-ActiveProductWorkers)
  if($workers.Count -gt 0){
    return @{status="DEFERRED_ACTIVE_PRODUCT_WORKERS";consume=$false;worker_count=$workers.Count}
  }
  git -C $ActorRepo checkout -B main origin/main --quiet
  if($LASTEXITCODE -ne 0){
    return @{status="SYNC_CHECKOUT_FAILED";consume=$true;worker_count=0}
  }
  git -C $ActorRepo reset --hard origin/main --quiet
  if($LASTEXITCODE -ne 0){
    return @{status="SYNC_RESET_FAILED";consume=$true;worker_count=0}
  }
  return @{status="COMPLETED";consume=$true;worker_count=0}
}

function Invoke-SafeSupervisorKick {
  if(Test-HumanMode){
    return @{status="HUMAN_MODE_ACTIVE";consume=$true;worker_count=@(Get-ActiveProductWorkers).Count}
  }
  & (Join-Path $env:LOCALAPPDATA "ExponentialEngine\Watch-ContinuousFlow.ps1")|Out-Null
  return @{status="COMPLETED";consume=$true;worker_count=@(Get-ActiveProductWorkers).Count}
}

function Invoke-SafeSupervisorRestart {
  if(Test-HumanMode){
    return @{status="HUMAN_MODE_ACTIVE";consume=$true;worker_count=@(Get-ActiveProductWorkers).Count}
  }
  $workers=@(Get-ActiveProductWorkers)
  if($workers.Count -gt 0){
    return @{status="DEFERRED_ACTIVE_PRODUCT_WORKERS";consume=$false;worker_count=$workers.Count}
  }
  $p=Get-CimInstance Win32_Process|Where-Object{
    $_.CommandLine -like "*continuous_flow_supervisor.py*" -and $_.Name -like "python*"
  }|Select-Object -First 1
  $workers=@(Get-ActiveProductWorkers)
  if($workers.Count -gt 0){
    return @{status="DEFERRED_ACTIVE_PRODUCT_WORKERS";consume=$false;worker_count=$workers.Count}
  }
  if($p){Stop-Process -Id $p.ProcessId -Force}
  Start-Sleep 2
  & (Join-Path $env:LOCALAPPDATA "ExponentialEngine\Watch-ContinuousFlow.ps1")|Out-Null
  return @{status="COMPLETED";consume=$true;worker_count=0}
}

function Invoke-RemoteControlRestart {
  $tasks=@(Get-ScheduledTask|Where-Object{
    $_.TaskName -match 'Azathoth.*Remote|Remote.*Desktop.*Commander|Desktop.*Commander'
  })
  if($tasks.Count -eq 0){return @{status="REMOTE_TASK_NOT_FOUND";consume=$true;worker_count=0}}
  foreach($task in $tasks){
    Enable-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath|Out-Null
    Stop-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath
  }
  return @{status="COMPLETED";consume=$true;worker_count=0}
}

function Invoke-ControlPlaneRecovery {
  $remote=Invoke-RemoteControlRestart
  if($remote.status -ne "COMPLETED"){return $remote}
  if(Test-HumanMode){
    return @{status="HUMAN_MODE_ACTIVE";consume=$true;worker_count=@(Get-ActiveProductWorkers).Count}
  }
  return Invoke-SafeSupervisorKick
}

function Invoke-SafeMainDeploy {
  $sync=Invoke-SafeActorSync
  if($sync.status -ne "COMPLETED"){return $sync}
  $restart=Invoke-SafeSupervisorRestart
  if($restart.status -ne "COMPLETED"){return $restart}
  return @{status="COMPLETED";consume=$true;worker_count=0}
}

while($true){
  try{
    git -C $ControlRepo pull --ff-only --quiet
    $c=Get-Content (Join-Path $ControlRepo "control\mailbox.json") -Raw|ConvertFrom-Json
    $prior=$null;if(Test-Path $state){$prior=Get-Content $state -Raw|ConvertFrom-Json}
    if($c.command_id -and $c.command_id -ne $prior.command_id){
      $now=[DateTimeOffset]::UtcNow;$exp=[DateTimeOffset]::Parse($c.expires_at)
      Write-Progress $c.command_id $c.action "ACKNOWLEDGED"
      if($now -gt $exp){
        Publish @{command_id=$c.command_id;action=$c.action;status="REJECTED_EXPIRED";at=$now.ToString("o")}
      }
      elseif($allowed -notcontains $c.action){
        Publish @{command_id=$c.command_id;action=$c.action;status="REJECTED_ACTION";at=$now.ToString("o")}
      }
      else{
        Write-Progress $c.command_id $c.action "EXECUTING"
        $result=@{status="COMPLETED";consume=$true;worker_count=0}
        switch($c.action){
          "NO_OP" {}
          "STATUS_SNAPSHOT" {
            $workers=@(Get-ActiveProductWorkers)
            @{recorded_at=$now.ToString("o");
              actor_head=(git -C $ActorRepo rev-parse HEAD);
              actor_branch=(git -C $ActorRepo branch --show-current);
              product_worker_count=$workers.Count;
              product_workers=@($workers|Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine);
              supervisor=@(Get-CimInstance Win32_Process|Where-Object{
                $_.CommandLine -like "*continuous_flow_supervisor.py*"
              }|Select-Object ProcessId,CreationDate)
            }|ConvertTo-Json -Depth 8|Set-Content -Encoding UTF8 (Join-Path $StateRoot "fallback-status.json")
          }
          "SYNC_ACTOR_MAIN" {$result=Invoke-SafeActorSync}
          "KICK_SUPERVISOR" {$result=Invoke-SafeSupervisorKick}
          "RESTART_SUPERVISOR" {$result=Invoke-SafeSupervisorRestart}
          "DEPLOY_MAIN_SAFE" {$result=Invoke-SafeMainDeploy}
          "RESTART_REMOTE_CONTROL" {$result=Invoke-RemoteControlRestart}
          "RECOVER_CONTROL_PLANE" {$result=Invoke-ControlPlaneRecovery}
          "HUMAN_MODE" {
            @{enabled=$true;since=[DateTimeOffset]::UtcNow.ToString("o");source="iphone-dashboard"} |
              ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $humanMode
            Set-WorkerTasksEnabled $false
            Stop-AutonomousProcesses
            $result=@{status="COMPLETED";consume=$true;worker_count=@(Get-ActiveProductWorkers).Count}
          }
          "FULL_POWER" {
            Remove-Item -Force $humanMode -ErrorAction SilentlyContinue
            Set-WorkerTasksEnabled $true
            $result=Invoke-SafeSupervisorKick
          }
          "REBOOT_WINDOWS" {
            $result=@{status="REBOOT_ACCEPTED";consume=$true;worker_count=@(Get-ActiveProductWorkers).Count}
            shutdown.exe /r /t 15 /d p:4:1 /c "Azathoth authenticated control-plane reboot"
          }
        }
        $receipt=@{
          command_id=$c.command_id;action=$c.action;status=$result.status;
          worker_count=$result.worker_count;at=[DateTimeOffset]::UtcNow.ToString("o")
        }
        Publish $receipt ([bool]$result.consume)
      }
    }
  }catch{ Start-Sleep 5 }
  Start-Sleep 30
}
