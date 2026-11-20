# IRON CONTINUUM — learning feedback with observed outcomes and no false promotion

## Gap addressed

Iron God already stores real worker attempts in the canonical
\`iron-god-products/development-memory.sqlite3\` and supplies catalogue-only
failure guidance to authorized Level-1 retries. In contrast, the separate
JEV selector learner reads \`telemetry/jev-shadow/decisions.jsonl\`; this
source is absent on some real deployments, leaving decision-learning with
zero attributed outcomes. Worker evidence must **never** be fabricated into
JEV selector or independent GitHub/product acceptance evidence.

## New, additive, canonical SQLite provenance

The existing development-memory SQLite database now contains an append-only
\`strategy_exposures\` table. A row is recorded only when a worker packet
actually carried an existing catalogue recovery strategy, citing a prior
immutable failed packet from the *same repository and work item*. The
advice must match the recorded failure classification and the Level-1
worker build scope. Packet replays cannot add or replace attributed guidance.
The original \`attempts\` and \`rules\` records remain unchanged.

This tracks *exposure*, not proof that the model complied with instructions
or that an improved result was caused by the advice.

## Read-only effectiveness projection

\`iron_god_learning_feedback.snapshot()\` uses only a bounded, read-only SQLite
connection, counts independent task lineages (not \`-r1\`, \`-r2\` retries),
and separates worker-reported local acceptance from failures/unjudged
results. It compares descriptive first-attempt baseline counts and exposed
strategies **without asserting a causal benefit**.

A strategy with three independent lineages and two locally accepted
outcomes becomes \`CANDIDATE_FOR_INDEPENDENT_REVIEW\`. This is **not**
automatic policy promotion. New deterministic checks require independent
exact-head CI, review, native acceptance and an explicit authorized code
change. Negative evidence remains visible; any measured regression should
prevent a proposed future promotion.

The existing supervisor's \`decision-learning.json\` now reports
\`development_feedback\` separately from JEV \`attributed_outcomes\`. The
existing MCP read-only \`get_engineering_evidence\` includes the same sanitized
\`learning.effectiveness\` summary. No new process, database, MCP server,
owner grant, model invocation, or network listener is introduced.

## Inspect without affecting workers

\`\`\`powershell
python -m project_orchestrator.iron_god_development_memory feedback
\`\`\`

## Prove it before relying on it

\`\`\`sh
python -m pytest -q tests/test_iron_god_learning_feedback.py tests/test_iron_god_development_memory.py tests/test_iron_continuum_sqlite_learning.py tests/test_decision_learning.py tests/test_iron_god_engineering_evidence.py
python -m ruff check src/project_orchestrator/iron_god_learning_feedback.py src/project_orchestrator/iron_god_development_memory.py src/project_orchestrator/supervisor_runtime.py tests/test_iron_god_learning_feedback.py
\`\`\`

Proof levels: (1) correct immutable attempt-to-strategy attribution, (2)
independent-worker local acceptance for strategies used, (3) externally
verified CI, PR merge, and native product acceptance. Only levels 1–2 are
measured here; **level 3 remains unverified**. Never mistake a worker PASS
for GitHub merge or product readiness, and never bypass owner approval,
local-only policy, memory floors, exact-head CI, or review gates.

Roll back the code by approved release process; the additive table may be
left in place and is ignored by earlier readers. No schema reset, historic
attempt deletion, or spontaneous reauthorization is required.
