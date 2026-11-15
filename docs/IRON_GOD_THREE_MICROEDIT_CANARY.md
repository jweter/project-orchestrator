# #710 disposable three-microedit restart canary

This is a **developer-side acceptance plumbing test**, not the live Ollama,
product repository, or engine restart proof.

After the code is reviewed and merged, an operator can run:

    python scripts/iron_god_three_microedit_canary.py

The command never launches the supervisor, Claude, or local Ollama and never
modifies production Git refs or owner control. It uses disposable temp folders
to initialize a fresh Git repository with three initially failing native
pytest acceptance tests and an owner-authorized disposable state root.

It adds three distinct source changes one at a time, runs real native pytest
tests that are red before each change and green afterward, writes per-head
FULL-mode test evidence, and feeds each result through the existing
local_acceptance prepare/finalize and protected Git compare-and-swap controls.

After the second accepted source edit it simulates Give Me Control and a
supervisor restart by closing and reopening the SQLite and protected Git ref
objects. It confirms no refill while owner control is denied, restores the
disposable owner authorization, checks that the same durable acceptance event
proposes the third slice without replaying the previous two, then records
the third verified edit. It checks the three distinct commit hashes, the
three real native test outcomes, the accepted ref, journal uniqueness and
reopening idempotence.

The CLI prints a JSON receipt that **explicitly says**
`live_ollama_proven=false`, `zero_cloud_token_use_proven=false`,
`product_reality_proven=false` and `engine_started=false`. Passing this
does **not** establish that Ollama wrote those edits, that development can
sustain 3+ changes under 15.7-GiB real RAM, or that owner controls/leases,
GitHub PR CI, native product acceptance, and full release gates are cleared.

## Owner/Claude restart notification

Do not turn on the paused Iron God engine solely because this disposable
canary passes or because Claude pulled a GitHub branch. The verified installed
pinned release SHA, explicit owner Take the Wheel, safe leases, local-only
Claude-blocking mode, Ollama RAM floor, a rollback plan, and exact-head CI
must be checked before any controlled *live* canary restart.

A full #710 deployment still additionally requires the live **Ollama-generated**
three meaningfully verified microedits, real interruption after the second,
idempotent recovery, progress on two repos with a third blocked, measured
zero cloud-model calls, and full native product PR/CI merge gates.

The existing DoWork Hourly owner's notification rule is responsible for
telling both the owner and Claude when those two readiness gates are
independently supported. This diagnostic does not send messages, schedule
jobs, or change running-engine state.
