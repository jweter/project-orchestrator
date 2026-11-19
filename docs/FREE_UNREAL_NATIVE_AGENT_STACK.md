# Iron God — governed free Unreal agent stack

This is the cross-repository dispatch companion to Everward's
docs/integrations/FREE_UNREAL_AGENT_STACK_2026-11-19.md.

Eligible Everward unreal_playtest and packaged_app Product Reality jobs
now advertise a free native runner profile. This is a *routing hint*, not
a new executor, lease, owner permission, or verified test result.

Module: src/project_orchestrator/iron_god_unreal_agent_stack.py

The adapter constructs only the approved command:
  python tools/unreal_free_agent_stack.py --mode run-native ...

Its existing supervised Windows runtime handles the invocation. It
requires a callable **live** authority check, rechecks owner authority
before/while/after the run through execute_supervised, pins exact
commit in job and evidence, and rejects any other repository,
unreviewed suite, arbitrary test command, missing script or use of a
worktree as the evidence destination.

Supported free suites: UBT, native Unreal Automation Tests, an existing
named Gauntlet test, and Windows Development packaging. An authorized
job does not automatically install Gauntlet fixtures, create screenshot
baselines or load/write to the running editor. Godot and Unreal source
changes still require independent approved slices and separate engines.

The Everward code itself verifies a clean linked worktree (not main or
the owner's primary checkout), disk/memory admission, exclusive test
lock and installed editor before spawning a native process. This
command must not be invoked directly by scheduled/ungoverned agents:
call execute_authorized only after **real** engine permission and
lease checks, not by passing an unvalidated boolean.

The returned verdict never grants Product Reality or merge authority.
Failure/missing report/timeout remains explicit; a process exit code
of zero is insufficient. Review actual Unreal reports and recorder
outcomes before advancing the release gate. The existing merge/review
checks and scheduled Iron God cadence remain unchanged.

The vendored 31 UE 5.8 reference skills are Markdown with an MIT
license and SHA-256 content lock. No paid services are needed.
Do not enable Aura Autopilot, Claude paid transport, hosted Semantic
Search providers, unrestricted editor MCP tool execution, or a
permanent MCP listener. Owner-only spending and Give Me Control /
Take the Wheel continue to govern work. The new code never calls
these prohibited providers.

This is an incremental deployment:
(1) skills and free native runner in Everward,
(2) governed dispatcher and tests here,
(3) optional real editor MCP read-only verification on Azathoth,
(4) native exact-head tests with real registered test names when
owner/lease/memory gates admit a worker.

Unit gate: tests/test_iron_god_unreal_agent_stack.py and
tests/test_product_reality_dispatch.py. Product Reality requires
a separate local Windows Unreal run, not just passing these tests.
