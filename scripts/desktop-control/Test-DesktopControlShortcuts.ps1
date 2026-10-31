# Read-only structural and live-guard checks for two desktop switches.
$ErrorActionPreference='Stop'
$base=Join-Path $env:LOCALAPPDATA 'ExponentialEngine'
$desktop=[Environment]::GetFolderPath('Desktop')
$marker=Join-Path $base 'flow-state\human-mode.json'
$vbs=Join-Path $base 'DesktopControlSilent.vbs'
$human=Join-Path $base 'Enter-HumanMode.ps1'
$wheel=Join-Path $base 'Take-The-Wheel.ps1'
$gate=Join-Path $base 'Check-Development-ResumeReady.ps1'
$checks=New-Object 'System.Collections.Generic.List[object]'
function Check([string]$id,[bool]$ok){$script:checks.Add([pscustomobject]@{name=$id;ok=$ok})}
$before=if(Test-Path $marker){[IO.File]::ReadAllText($marker)}else{'<MISSING>'}
$w=New-Object -ComObject WScript.Shell
foreach($e in @(@('1 - GIVE ME CONTROL.lnk','human'),@('2 - TAKE THE WHEEL.lnk','wheel'))){
 $p=Join-Path $desktop $e[0];$ok=$false
 if(Test-Path $p){
  $lnk=$w.CreateShortcut($p)
  $expected='"'+$vbs+'" '+$e[1]
  $ok=([IO.Path]::GetFileName($lnk.TargetPath) -ieq 'wscript.exe' -and
        $lnk.Arguments -ceq $expected -and $lnk.WorkingDirectory -ieq $base -and
        (Test-Path -LiteralPath $lnk.TargetPath -PathType Leaf))
 }
 Check ('shortcut_'+$e[1]) $ok
}
foreach($p in @($human,$wheel,$gate)){
 $tokens=$null;$errors=$null
 if(Test-Path $p){[System.Management.Automation.Language.Parser]::ParseFile($p,[ref]$tokens,[ref]$errors)|Out-Null}
 Check ('powershell_syntax_'+[IO.Path]::GetFileName($p)) ((Test-Path $p)-and $errors.Count -eq 0)
}
if(Test-Path $vbs){
 $text=[IO.File]::ReadAllText($vbs)
 Check 'instant_owner_veto_precedes_script_launch' ($text.IndexOf('stream.WriteLine') -ge 0 -and $text.IndexOf('stream.WriteLine') -lt $text.IndexOf('sh.Run'))
 Check 'both_vbs_routes_exist' ($text.Contains('mode = "human"') -and $text.Contains('mode = "wheel"') -and $text.Contains('Enter-HumanMode.ps1') -and $text.Contains('Take-The-Wheel.ps1'))
}else{Check 'vbs_exists' $false}
if(Test-Path $human){
 $text=[IO.File]::ReadAllText($human)
 Check 'human_can_stop_workers_and_tasks' ($text.Contains('Find-OwnerWorkers') -and $text.Contains('Stop-Process') -and $text.Contains('Disable-ScheduledTask'))
}
if(Test-Path $wheel){
 $text=[IO.File]::ReadAllText($wheel)
 $grant=$text.IndexOf("'issue','--action','TAKE_THE_WHEEL'")
 Check 'release_guard_precedes_owner_grant' ($grant -gt 0 -and $text.IndexOf('Check-Development-ResumeReady.ps1') -ge 0 -and $text.IndexOf('Check-Development-ResumeReady.ps1') -lt $grant)
 Check 'wheel_success_requires_supervisor_ack' ($text.Contains("'wait-ack'") -and $text.IndexOf("'wait-ack'") -lt $text.IndexOf('TAKE_THE_WHEEL_CONFIRMED'))
 Check 'wheel_keeps_rocksmith_veto' ($text.Contains("Get-Process -Name 'Rocksmith2014'"))
 Check 'wheel_requires_owner_confirmation' ($text.Contains("'YesNo'") -and $text.Contains('[System.Windows.MessageBox]::Show('))
}
$ready=$null
if(Test-Path $gate){
 $output=@(& powershell.exe -NoProfile -NonInteractive -File $gate 2>&1)
 $code=$LASTEXITCODE
 try{
  $ready=($output|Out-String|ConvertFrom-Json)
  Check 'release_guard_returns_typed_state' ($ready.status -in @('RESUME_READY','RESUME_BLOCKED'))
  Check 'release_guard_exit_matches_readiness' (($code -eq 0) -eq [bool]$ready.ready)
 }catch{Check 'release_guard_json_parses' $false}
}
$after=if(Test-Path $marker){[IO.File]::ReadAllText($marker)}else{'<MISSING>'}
Check 'owner_marker_preserved' ($before -ceq $after)
$bad=@($checks|Where-Object{-not $_.ok})
[pscustomobject]@{
  status=if($bad.Count){'SHORTCUT_CHECK_FAILED'}else{'SHORTCUT_WIRING_VERIFIED'}
  checks=$checks.Count;passed=$checks.Count-$bad.Count;failed=$bad.Count
  failed_checks=@($bad|ForEach-Object{$_.name})
  resume_status=if($ready){$ready.status}else{'UNKNOWN'}
  resume_blockers=if($ready){@($ready.issues)}else{@('GUARD_UNVERIFIED')}
  owner_mode_unchanged=$true
} | ConvertTo-Json -Depth 5
if($bad.Count){exit 3}
exit 0
