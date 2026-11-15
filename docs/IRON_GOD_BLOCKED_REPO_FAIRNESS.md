# Issue #710 — two-repository progress under a third repository blocker

This is a product-lane integration test, **not** a new task planner,
scheduling agent, or change to live configuration.

The test creates three disposable Git-backed portfolio repositories using
the real product-lane test fixture. The third repository has a not-ready
verdict tied to its exact main-branch SHA, representing pending CI.
The other two repositories use the existing rotating scheduler, local
worker, native tests and durable packet acceptance and must both achieve
ACCEPTED_LOCAL while the third does not start a worker or claim an attempt.

The test then changes the third repository's readiness verdict to ready at
the same SHA and demonstrates that it gets a subsequent accepted development
turn without resetting the rotation or the other two completed attempts.

This directly exercises the existing anti-starvation behavior and correct
blocking/unblocking. It uses in-memory fake GitHub PR operations and
fixture-generated code; it is **not** a live three-repository Ollama
throughput, full CI, cloud-isolation or deployment proof. Those require
owner-approved installed engine canaries and native product acceptance.
