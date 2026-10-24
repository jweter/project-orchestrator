' Owner-first Windows control launcher: never open a visible console.
Option Explicit
Dim sh, fso, here, script, cmd, mode, marker, stateDir, stream, suffix
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
If WScript.Arguments.Count <> 1 Then WScript.Quit 2
mode = LCase(WScript.Arguments(0))
suffix = ""
If mode = "human" Then
    stateDir = fso.BuildPath(here, "flow-state")
    If Not fso.FolderExists(stateDir) Then fso.CreateFolder stateDir
    marker = fso.BuildPath(stateDir, "human-mode.json")
    ' Owner veto takes effect synchronously before any PowerShell startup.
    Set stream = fso.OpenTextFile(marker, 2, True)
    stream.WriteLine "{""enabled"":true,""source"":""desktop-instant-veto""}"
    stream.Close
    script = fso.BuildPath(here,"Enter-HumanMode.ps1")
    ' Stop autonomous work without disconnecting the owner's
    ' authenticated Desktop Commander session needed for recovery.
    suffix = " -KeepRemote"
ElseIf mode = "wheel" Then
    script = fso.BuildPath(here,"Take-The-Wheel.ps1")
    ' The owner clicking this desktop icon is the explicit consent.
    ' Keep the Rocksmith veto, release checks, task gates and rollback inside PS1.
    suffix = " -ExplicitUserAuthorization"
ElseIf mode = "testhuman" Then
    script = fso.BuildPath(here,"Enter-HumanMode.ps1")
    suffix = " -SelfTest"
ElseIf mode = "testwheel" Then
    script = fso.BuildPath(here,"Take-The-Wheel.ps1")
    suffix = " -SelfTest"
ElseIf mode = "repair" Then
    script = fso.BuildPath(here,"Fix-DesktopControls.ps1")
    suffix = " -ShowConfirmation"
Else
    WScript.Quit 2
End If
If Not fso.FileExists(script) Then WScript.Quit 3
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & script & Chr(34) & suffix
Dim result
result = sh.Run(cmd, 0, True)
If result = 0 Then
    ' Successful Take the Wheel handoff is silent: one click, no modal dialog.
    If mode <> "wheel" Then
        MsgBox "Control command completed successfully.", 64, "Azathoth Control"
    End If
ElseIf mode = "wheel" And result = 5 Then
    MsgBox "Iron God is currently stopped for a verified local-release upgrade." & vbCrLf & _
           "Take the Wheel will not restart the old supervisor." & vbCrLf & _
           "The release, watchdog, and startup integration must pass validation first." & vbCrLf & _
           "Give Me Control remains available at any time.", 48, "Take the Wheel - Upgrade Pending"
ElseIf mode = "wheel" And result = 6 Then
    MsgBox "Development startup could not be verified." & vbCrLf & _
           "Check flow-state\control-switch.log and desktop-resume-runtime.json." & vbCrLf & _
           "Give Me Control remains available.", 48, "Take the Wheel - Startup Unverified"
ElseIf mode = "wheel" And result = 7 Then
    MsgBox "Owner control was requested during startup. Development handoff cancelled.", 48, "Azathoth Control"
Else
    MsgBox "Control command failed. Exit code: " & result, 16, "Azathoth Control"
End If

