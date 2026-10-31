# Canon S2 — Native preflight failure diagnostics

**Scope:** A single source/test slice to preserve actionable traceback
evidence for failed Windows-native preflight checks. The previous S2 native
full suite on commit `0813bda` recorded 13 failures and 2,827 passes, but
`engineering/preflight.py` retained only the last 4,000 characters of each
check in ignored `preflight-evidence.json`. That discarded earlier failure
tracebacks, including the unexplained Git worktree-cleanup failure.

## Implemented behavior (source candidate only)

The existing preflight command, checks, policy, timeout values, gate order
and exact-head fencing are unchanged. Each nonzero check (including timeout
and interrupted checks) now writes a uniquely named local diagnostic under:

`%TEMP%\ig-preflight-failures\<worktree-key>\<check>-<id>.log`

The usual ignored `preflight-evidence.json` retains the same compact
`stdout` and `stderr` tail projections. The result also includes:

- `failure_diagnostic.path`: absolute path of the locally retained log.
- `failure_diagnostic.stdout_original_chars` and `stderr_original_chars`.
- `failure_diagnostic.stdout_omitted_chars` and `stderr_omitted_chars`:
  explicit nonzero values whenever a single diagnostic stream exceeds the
  two-million-character retention limit (first and last halves retained).
- `failed_test_nodes`: up to 100 deduplicated `FAILED tests/...::...`
  node identifiers extracted from the complete pytest output.
- `failure_diagnostic.error`: specific error classification if local
  diagnostic creation failed; **the check remains red**.

Logs are **local-only**, may contain sensitive paths or data, and must never
be copied into a repository, GitHub issue, public CI artifact, or response
without separate sanitization and authorization. Best-effort credential
redaction is not a comprehensive secrets/privacy guarantee. The worktree
hash prevents cross-worktree log collisions; random filenames prevent
clobbering an earlier failure. The existing pytest scratch cleanup remains
unchanged and does not delete these distinct diagnostics.

If the full suite fails, read `results[]` for the failing check and its
`failure_diagnostic.path`, then inspect that private local log. **No test
is skipped, no owner gate is weakened, and failed work does not become
accepted work.** The prior full-suite failure does not become explained
merely because the diagnostics repair passes its own focused tests.

## Acceptance

Source checks cover early traceback retention past the 4,000-character
projection, real child subprocess failure, Windows bytes timeout,
truncation, JSON pointer, redaction, unique run identifiers, clean-check
silence, and I/O failure preserving the red verdict.

The source candidate still needs independently authorized native exact-head
full preflight and owner/controller acceptance before merging or deployment.
S3 security work is not part of this slice.
