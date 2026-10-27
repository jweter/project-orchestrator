# Iron God: zero-fee local builder qualification

This is a **separate experimental harness**, not an Iron God release. It was
developed in its own Git worktree starting at the repository's remote main.
It neither changes desktop controls nor issues owner grants.

## Principles

- Use only locally hosted Ollama models, with no paid APIs or credentials.
- Qualify code locally first. A candidate's passing generated tests **do not**
  qualify code for GitHub; independent tests and canonical repository preflight
  are required. CI must be green before merging PRs.
- Compare against Aider, the existing baseline. Other candidates: Pi,
  mini-SWE-agent, Codex CLI, OpenCode, Kilo, Goose, OpenHands, and Claude Code
  configured explicitly for local Ollama. Every tool must be proven compatible
  before any shell-capable agent receives execution permission.
- Tool-using agents may write outside their current directory. An isolated Git
  worktree is **not** an OS sandbox. Until a WSL/container/VM boundary is ready,
  perform version and configuration probes only; do **not** launch unrestricted
  agents against production repositories.
- Use one agent and one model at a time. Never run inference from the benchmark
  while an Iron God supervisor has a fresh active heartbeat.
- Preserve owner authority, exact Git head, worker-resource leases, native tests,
  verification, grant enforcement, PR policy, and local-first development.
- Do not modify the production model catalog, Claude subscription config,
  scheduled tasks, or installed Iron God runtime.

## Implemented qualification slice

A tiny self-contained Python repository tests beat-grid snapping, an actual
historical failure theme. Target file: `src/tempo.py`; tests are locked and
independently authored. Failure to edit a file, out-of-scope edits, failing
behavior, malformed JSON, and unsafe paths cause rejection.

The built-in restricted-patch adapter is **not** a full coding agent: Ollama
returns a JSON file proposal, and the harness applies only explicitly allowed
paths. This avoids giving the model unrestricted shell access.

### Commands (from the benchmark worktree)

```powershell
python engineering/free_builder_benchmark/benchmark.py inventory
python -m unittest discover -s tests -p test_free_builder_benchmark.py -v
python engineering/free_builder_benchmark/benchmark.py prepare --workspace C:\Temp\iron-god-benchmark-task
# If Iron God supervisor is stopped and RAM >= 3 GiB:
python engineering/free_builder_benchmark/benchmark.py local-patch --workspace C:\Temp\iron-god-benchmark-task --model qwen2.5-coder:7b
```

`local-patch` refuses to call Ollama when Iron God's supervisor heartbeat is
fresh or available memory is below the 3-GiB threshold.

## Sequential candidate qualification

1. **Install/version** in a separate tool directory, without replacing default
   binaries or altering user agent settings.
2. **Local provider check**: only `http://127.0.0.1:11434` and installed
   Ollama models; no account sign-in, trials, credits, paid gateway or cloud.
3. **Isolation check**: OS sandbox required before tool/shell based coding.
4. **Same task**: one bounded task, same initial Git commit, independent tests.
5. **Evidence**: exact candidate/package version, model/tag, elapsed time,
   peak memory, paths touched, test results, independent review, and Git SHA.
6. **Acceptance**: do not merge code unless it advances a real roadmap item
   and its canonical native preflight and remote CI pass.

### Known environment constraint

Azathoth has 16 GiB physical memory; 7B local-model inference can leave less
than 3 GiB available. OpenHands requires WSL/Docker and should not be started
concurrently with an active worker. Neither a tool's successful installation
nor its CLI response is proof that it generates correct code.

## Promotion rule

A green isolated benchmark is evidence only for **candidate selection**.
It does not authorize live Iron God runtime modification, model concurrency
increase, or bypassing owner-control. Prepare a focused PR for the harness
separately; merge only after GitHub CI is green and applicable safety gates pass.


## Azathoth installation/compatibility inventory (October 27, 2026)

The following were installed to isolated directories under
`%LOCALAPPDATA%\IronGodBuilderBench` without globally replacing user CLI
commands or starting an LLM inference session:

| Candidate | Installed / CLI smoke | Source-based coding quality |
| --- | --- | --- |
| Existing Aider + Ollama | Installed beforehand | Historical accepted commits; new controlled test pending |
| Pi Coding Agent | 0.73.1, version command successful | Not yet tested |
| Codex CLI (local OSS mode) | 0.164.0, version command successful | Not yet tested |
| OpenCode | 1.19.4, binary validated after official local postinstall | Not yet tested |
| Kilo CLI | 7.8.18, version command successful | Not yet tested |
| Goose CLI | 1.56.0, official Windows binary verified | Not yet tested |
| mini-SWE-agent | 2.4.6 installed; interactive CLI requires Windows console unavailable over Remote Desktop Commander | Not yet tested |
| OpenHands | Not installed; WSL Ubuntu absent and Docker daemon stopped | Not yet tested |
| Claude Code + Ollama | Claude Code installed but configured user session not disturbed | Not yet tested |
| Restricted-patch builder | Built-in allowlisted proposal protocol, 10 unit tests green | Independent safety fixture passed; local-model inference pending |

`qwen2.5-coder:7b`, `qwen2.5-coder:1.5b`, and `qwen3.5:9b` are
already installed in Ollama. The benchmark did not download new model weights.

**The live supervisor is running**, and RAM dropped below 3 GiB during coding
work. To avoid competing with active workers, no additional Ollama inference
was launched by this benchmark. Installation and version checks alone **do
not** establish which candidate produces better code.

Cloud API free tiers are excluded from automatic execution: they are
quota-limited and would transmit source code outside the laptop. No API
credentials, subscriptions, payment setup, or paid providers were used.
