# Disposable Agent Evaluation Matrix Runner

## Purpose

The matrix runner operationalizes the deterministic agent-evaluation suite without changing product authority. It runs the same pinned regression tasks against one or more explicitly supplied **local** coding-agent configurations in disposable git worktrees, records deterministic outcomes, and feeds the existing canonical KPI event ledger.

It does not decide merges, does not infer Product Reality, does not score subjective quality, and does not create a second metrics store.

## Safety model

Each task execution follows this sequence:

1. verify the task baseline is an exact 40-character commit SHA available locally;
2. create a detached disposable worktree at that exact baseline;
3. apply the committed fault fixture;
4. run the task's first deterministic verification command and require it to fail;
5. if the injected fault does **not** make the contract RED, stop as `INVALID_BENCHMARK` before invoking the agent;
6. write the task prompt into the disposable worktree;
7. invoke the configured agent directly with `subprocess(..., shell=False)`;
8. run repository-native deterministic verification commands;
9. write the Phase C attempt record and local evidence artifacts;
10. append the attempt to the existing KPI ledger through `agent_eval.record_attempt` unless `--no-record` is requested;
11. remove and prune the disposable worktree.

The runner never writes the injected fault into `main` or another valuable working tree.

## Local agent configuration

Runner configuration is local JSON with this schema:

```json
{
  "schema_version": 1,
  "configurations": [
    {
      "agent_id": "local-agent-a",
      "agent_config_id": "baseline-config",
      "argv": ["agent-a", "--prompt-file", "{prompt_file}"],
      "timeout_seconds": 1800
    },
    {
      "agent_id": "local-agent-b",
      "agent_config_id": "comparison-config",
      "argv": ["agent-b", "--prompt-file", "{prompt_file}"],
      "timeout_seconds": 1800
    }
  ]
}
```

The committed `engineering/examples/agent-eval-runner.example.json` is syntax-only. It intentionally does not assume that any particular external agent CLI is installed.

### Command restrictions

- `argv` is an argument vector, not a shell command string.
- common shell executables (`bash`, `sh`, `cmd`, PowerShell, etc.) are rejected;
- timeout must be bounded between 1 and 7200 seconds;
- credentials are inherited from the local execution environment and must never be committed to the runner config;
- local output is ignored under `.agent-eval-runs/`.

Supported argv placeholders are:

- `{prompt_file}`
- `{worktree}`
- `{output_dir}`
- `{task_id}`
- `{evaluation_run_id}`

The same values are also provided through `AGENT_EVAL_*` environment variables for agent CLIs that prefer environment-driven integration.

## Validate configuration

```bash
project-orchestrator agent-eval-runner validate-config engineering/examples/agent-eval-runner.example.json
```

Validation confirms the schema, bounded timeout, argv-only execution, and non-authoritative safety properties. It does not claim the external executable exists.

## Run a matrix

For the current project-orchestrator suite:

```bash
project-orchestrator agent-eval-runner run \
  --config private/agent-eval-local.json \
  --repo jweter/project-orchestrator=/path/to/project-orchestrator
```

The default suite is `engineering/agent-eval-suite.json`, the default ledger is `engineering/kpi-events.jsonl`, and local artifacts are written under `.agent-eval-runs/<matrix-id>/`.

Use repeated `--repo owner/name=/path` arguments when a future suite contains tasks from multiple repositories.

Run only selected tasks with repeated `--task`:

```bash
project-orchestrator agent-eval-runner run \
  --config private/agent-eval-local.json \
  --repo jweter/project-orchestrator=/path/to/project-orchestrator \
  --task loop-self-judging \
  --task kpi-unknown-preservation
```

Use `--no-record` for an isolated dry measurement that should not append KPI events.

## Evidence and comparison

Each attempt produces local JSON evidence for:

- fault-to-RED proof;
- agent invocation and timeout/return code;
- each deterministic verification command;
- the normalized Phase C attempt record;
- a runner manifest.

A matrix summary records pass/fail/invalid-benchmark counts. Durable comparison still uses the existing `project-orchestrator agent-eval report` layer, which only marks agent configurations comparable when suite, baseline, measurement contract, and task coverage match.

No missing observation is converted to zero or PASS.

## Product Reality boundary

The runner accepts only the existing deterministic Phase C suite. It cannot convert visual quality, musical interpretation, gameplay feel, tone/fingering, subjective scientific judgment, or other Product Reality into benchmark acceptance.

`product_reality_inferred` is always false and `merge_authority` is always false in runner outputs.

## Operational use

The intended first real experiment is to configure two local coding-agent setups, run all three pinned tasks against both configurations, then query the existing agent-evaluation report and KPI ledger. The resulting evidence can support decisions about configuration defaults, regression protection, and whether Phase D repository-specific skill extraction is justified.
