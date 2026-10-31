# Retired GitHub-first installer. Never re-enable origin/main actor scheduling.
# Production startup now requires a separately validated local accepted release,
# owner authorization, an exact release receipt, and approved cutover.
# Kept as a diagnostic, fail-closed compatibility entrypoint so old scheduled
# workflows cannot accidentally reinstall stale code.
param()
$ErrorActionPreference = 'Stop'
throw 'LEGACY_GITHUB_ACTOR_INSTALLER_DISABLED_USE_PINNED_LOCAL_RELEASE'
