# Jev Runtime Evidence

The runtime ledger makes Jev shadow measurement persistent during normal unattended orchestration.

Default path:

`telemetry/jev-shadow/decisions.jsonl`

The runtime cycle accepts a stable orchestration `run_id`, the emitted Foreman plan, and sanitized worker/orchestrator results. It performs two operations:

1. capture new meaningful multi-candidate shadow decisions idempotently;
2. reconcile explicit terminal worker/orchestrator evidence against open records.

Writes use a temporary file, flush/fsync, and atomic replace so a partial rewrite does not become valid evidence. Replaying the same run does not duplicate its project decision record. Pending results do not rewrite the ledger.

CLI:

`project-orchestrator jev-runtime-cycle evidence.json`

Input shape:

```json
{
  "run_id": "stable-run-identity",
  "foreman_plan": {},
  "results": []
}
```

This runtime is telemetry only. It has no execution, dispatch, promotion, merge, or Product Reality authority.
