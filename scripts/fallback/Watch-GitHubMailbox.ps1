param(
  [string]$ActorRepo=(Join-Path $env:LOCALAPPDATA "ExponentialEngine\actor-repo"),
  [string]$StateRoot=(Join-Path $env:LOCALAPPDATA "ExponentialEngine\flow-state"),
  [string]$ControlRepo=(Join-Path $env:LOCALAPPDATA "ExponentialEngine\FallbackControl\control-repo")
)
$ErrorActionPreference="Stop"
$root=Join-Path $env:LOCALAPPDATA "ExponentialEngine\FallbackControl"
$state=Join-Path $root "state.json"
$allowed=@("NO_OP","STATUS_SNAPSHOT","SYNC_ACTOR_MAIN","KICK_SUPERVISOR","RESTART_SUPERVISOR")
function Publish($o){
  $json=$o|ConvertTo-Json -Depth 8
  $json|Set-Content -Encoding UTF8 $state
  $json|Set-Content -Encoding UTF8 (Join-Path $ControlRepo "control\receipt.json")
  git -C $ControlRepo add control/receipt.json
  git -C $ControlRepo commit -m ("control: receipt "+$o.command_id) --quiet 2>$null
  git -C $ControlRepo push origin azathoth-control --quiet
}
while($true){
  try{
    git -C $ControlRepo pull --ff-only --quiet
    $c=Get-Content (Join-Path $ControlRepo "control\mailbox.json") -Raw|ConvertFrom-Json
    $prior=$null;if(Test-Path $state){$prior=Get-Content $state -Raw|ConvertFrom-Json}
    if($c.command_id -and $c.command_id -ne $prior.command_id){
      $now=[DateTimeOffset]::UtcNow;$exp=[DateTimeOffset]::Parse($c.expires_at)
      if($now -gt $exp){Publish @{command_id=$c.command_id;status="REJECTED_EXPIRED";at=$now.ToString("o")}}
      elseif($allowed -notcontains $c.action){Publish @{command_id=$c.command_id;status="REJECTED_ACTION";action=$c.action;at=$now.ToString("o")}}
      else{
        switch($c.action){
          "NO_OP" {}
          "STATUS_SNAPSHOT" {
            @{recorded_at=$now.ToString("o");actor_head=(git -C $ActorRepo rev-parse HEAD);
              supervisor=@(Get-CimInstance Win32_Process|Where-Object{$_.CommandLine -like "*continuous_flow_supervisor.py*"}|Select-Object ProcessId,CreationDate)
            }|ConvertTo-Json -Depth 6|Set-Content -Encoding UTF8 (Join-Path $StateRoot "fallback-status.json")
          }
          "SYNC_ACTOR_MAIN" {git -C $ActorRepo fetch origin main --quiet;git -C $ActorRepo reset --hard origin/main|Out-Null}
          "KICK_SUPERVISOR" {& (Join-Path $env:LOCALAPPDATA "ExponentialEngine\Watch-ContinuousFlow.ps1")}
          "RESTART_SUPERVISOR" {
            $p=Get-CimInstance Win32_Process|Where-Object{$_.CommandLine -like "*continuous_flow_supervisor.py*" -and $_.Name -like "python*"}|Select-Object -First 1
            if($p){Stop-Process -Id $p.ProcessId -Force};Start-Sleep 2
            & (Join-Path $env:LOCALAPPDATA "ExponentialEngine\Watch-ContinuousFlow.ps1")
          }
        }
        Publish @{command_id=$c.command_id;action=$c.action;status="COMPLETED";at=[DateTimeOffset]::UtcNow.ToString("o")}
      }
    }
  }catch{ Start-Sleep 5 }
  Start-Sleep 30
}