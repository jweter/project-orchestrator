# Azathoth phone control plane

The iPhone Foundry dashboard may dispatch a deliberately small set of administrative commands through the existing GitHub fallback-control mailbox.

## Security boundary

- The dashboard does not expose an arbitrary shell.
- `/api/control` requires a bearer token supplied through `AZATHOTH_DASHBOARD_CONTROL_TOKEN`.
- The browser stores that admin token locally on Jeremy's device; it is never committed.
- The server accepts only `STATUS_SNAPSHOT`, `RESTART_REMOTE_CONTROL`, `RECOVER_CONTROL_PLANE`, and `REBOOT_WINDOWS`.
- Mailbox commands expire after five minutes and retain the existing command/receipt audit trail.
- Reboot is explicit and the mobile UI requires confirmation.
- DoWork Hourly is outside this control surface and cannot be paused, disabled, deleted, replaced, or rescheduled here.

## Recovery independence

Desktop Commander is not the transport for these commands. The dashboard writes an authenticated, expiring command to the existing `azathoth-control` GitHub mailbox. The independently guarded mailbox watcher on Azathoth executes only allowlisted actions.

This means `RESTART_REMOTE_CONTROL` remains useful specifically when Desktop Commander itself is unavailable, provided Windows, the dashboard service, the GitHub mailbox watcher, and network access are alive.

A fully powered-off laptop still requires an out-of-band power mechanism such as verified Wake-on-LAN or firmware-supported AC-loss recovery; software running on Azathoth cannot power on a machine that has no running control path.
