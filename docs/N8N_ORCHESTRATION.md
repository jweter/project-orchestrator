# n8n Orchestration Boundary

## Purpose

n8n is an event-driven transport and workflow-dispatch layer around the existing portfolio control plane. It is not a second source of truth and is not the autonomous scheduler.

The authority order remains:

1. live GitHub/repository evidence;
2. each product repository's governing roadmap, architecture, vision, tests, CI, and control plane;
3. `DoWork.txt` and the portfolio control plane for cross-project prioritization;
4. n8n only as a transport/dispatch mechanism.

## Why n8n belongs here

The portfolio has independent GitHub repositories with shared operational patterns and one explicit dependency family: Knowledge Engine Core/Web/AI. GitHub Actions is the correct deterministic executor for repository-local verification. n8n adds value between repositories by reacting to events and routing bounded work without duplicating every cross-repository workflow in every product repository.

## v1 event paths

### PR CI failure

```text
GitHub check_suite
        |
        v
n8n GitHub Trigger
        |
        v
fail-closed classifier
        |
        v
orchestrator-observe.yml
        |
        v
sanitized REPAIR_CANDIDATE artifact
```

Only a completed terminal failure associated with a PR is dispatched. Success is not interpreted as merge authority, and neutral/skipped/missing/unknown states are not promoted to green.

### Knowledge Engine main change

```text
Core / Web / AI push to main
        |
        v
n8n GitHub Trigger
        |
        v
canonical-family + exact-SHA filter
        |
        v
orchestrator-observe.yml
        |
        v
DEPENDENCY_RECHECK_CANDIDATE artifact
```

The compatibility planner and live evidence collector remain responsible for deciding which dependent repositories actually need action.

## Mutation boundary

The repository validator permits the committed n8n workflows to perform exactly one GitHub mutation class: dispatch `orchestrator-observe.yml` in `jweter/project-orchestrator` on `main`.

The committed workflows cannot contain nodes that merge PRs, write product files, execute shell commands, make arbitrary network calls from Code nodes, alter branch protection, disable tests, or schedule recurring autonomous work. They also remain inactive and contain no credential references in Git.

This is intentionally narrower than n8n's full capability surface.

## Scheduler boundary

`DoWork Hourly` remains the single autonomous portfolio scheduler. n8n workflows in this repository contain no schedule trigger. Event delivery may wake the evidence-intake lane, but n8n does not independently select roadmap work or create a competing development cadence.

## Security boundary

Repository configuration is local-only by default. The Docker port binds to `127.0.0.1`; credential material lives only in the n8n runtime.

GitHub webhook activation requires a GitHub-reachable HTTPS URL. Public ingress is a security-boundary change and therefore requires Jeremy's explicit approval before configuration. The activation gate is:

- approved HTTPS ingress;
- least-privilege GitHub credential restricted to the portfolio;
- n8n security audit without unresolved high-risk findings;
- manual review of imported workflows;
- one-router-at-a-time activation and real webhook verification.

## Repair executor seam

The v1 n8n layer records a repair candidate; it does not yet modify code. A future repair executor may consume that candidate only after it fetches actual failing logs and exact PR-head evidence. Its required chain is unchanged:

`failure evidence -> first meaningful root cause -> smallest safe fix -> exact-head preflight -> push existing PR branch -> fresh CI`

No future n8n workflow may gain merge authority merely because it can dispatch or repair work.
