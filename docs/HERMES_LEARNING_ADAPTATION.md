# Hermes Learning Adaptation

Hermes confirms a useful separation: compact factual memory should stay distinct from larger procedural skills, and procedural skills should load only when relevant. Its agent-managed skills can be created or updated after non-trivial workflows and use progressive disclosure to control context cost.

Daedalus already has the stronger provenance side: repository skills are derived from verified lessons, bound to repository/history evidence, advisory-only, and cannot infer Product Reality. This slice adds the missing effectiveness/refinement loop rather than importing Hermes.

## Adapted mechanism
1. Select a verified-history-derived repository skill only when paths/task triggers match.
2. Record each actual application by unique run id.
3. Count success only when downstream verification passes.
4. Track whether reuse prevented a known failure fingerprint.
5. Keep low-sample skills in PILOT.
6. Promote sufficiently verified skills to PROVEN.
7. Mark repeatedly weak skills DEGRADED and stop injecting them until reviewed or retired.

This yields procedural learning with progressive reuse while preserving Daedalus evidence gates.
