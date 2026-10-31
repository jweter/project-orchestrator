# Staged clones can inherit administrator-only temporary-directory ACLs.
# Give only the scheduled-task owner access to these two exact release paths.
param([Parameter(Mandatory=$true)][string]$RepoRoot,
      [Parameter(Mandatory=$true)][string]$RuntimeRoot,
      [Parameter(Mandatory=$true)][string]$TaskUser)
$ErrorActionPreference='Stop'
$release=Get-Item -LiteralPath $RepoRoot
$runtime=Get-Item -LiteralPath $RuntimeRoot
if ($release.Name -cnotmatch '^[a-f0-9]{40}$' -or
    $runtime.Name -cne $release.Name -or
    $release.Parent.Name -cne 'releases' -or
    $runtime.Parent.Name -cne 'runtime-actors' -or
    $release.Parent.Parent.FullName -ine $runtime.Parent.Parent.FullName) {
  throw 'Access repair is restricted to matching SHA-specific release/runtime paths'
}
if (($release.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    ($runtime.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
  throw 'Refusing access repair through a reparse point'
}
$account=New-Object Security.Principal.NTAccount($TaskUser)
$sid=$account.Translate([Security.Principal.SecurityIdentifier]).Value
foreach($entry in @(@($release.FullName,'RX'),@($runtime.FullName,'M'))) {
  & icacls.exe $entry[0] /grant (('*{0}:(OI)(CI){1}' -f $sid,$entry[1])) /T /Q | Out-Null
  if($LASTEXITCODE -ne 0){throw 'Task-user access provisioning failed'}
  # Match Git ownership checks without global safe.directory exemptions.
  & icacls.exe $entry[0] /setowner ('*'+$sid) /T /Q | Out-Null
  if($LASTEXITCODE -ne 0){throw 'Task-user Git ownership provisioning failed'}
}
Write-Output 'LOCAL_RUNTIME_TASK_USER_ACCESS_READY'
