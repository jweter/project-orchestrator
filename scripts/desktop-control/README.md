# Azathoth desktop controls

These versioned sources deploy to `%LOCALAPPDATA%\ExponentialEngine`.
The two desktop shortcuts invoke `DesktopControlSilent.vbs human` and `wheel`.

Take the Wheel requires a clean, exact-head local release with a native full
preflight receipt, matching actor and supervisor pins, both scheduled-task
launchers, and retirement of the obsolete Startup entry. It starts only the
two validated engine tasks. It reports success only after observing one
supervisor on that release, a fresh matching heartbeat, and one pinned actor.
A task-start request alone does not establish a working engine.

Give Me Control writes the owner veto immediately and stops the engine,
including the pinned PowerShell launcher wrappers. Its normal desktop route
also disconnects remote control; `-KeepRemote` is available for an authorized
maintenance test. Never release an owner veto without owner authorization.

The release installer preserves the old actor checkout and uses a separate
SHA-specific runtime checkout. Development happens locally; publication to
GitHub is a separate backup/milestone operation.

`Test-DesktopControlShortcuts.ps1` performs read-only wiring checks and reports
release readiness separately. Runtime startup evidence is recorded locally in
`flow-state/desktop-resume-runtime.json`; do not commit machine state or logs.
