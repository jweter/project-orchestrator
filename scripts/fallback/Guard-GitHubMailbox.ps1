$ErrorActionPreference="SilentlyContinue"
$watcher=Join-Path $env:LOCALAPPDATA "ExponentialEngine\FallbackControl\Watch-GitHubMailbox.ps1"
while($true){
  $live=Get-CimInstance Win32_Process|Where-Object{$_.Name -like "powershell*" -and $_.CommandLine -like "*Watch-GitHubMailbox.ps1*"}|Select-Object -First 1
  if(-not $live){Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File",('"'+$watcher+'"'))}
  Start-Sleep 60
}