# Headroom Context-Compression Benchmark

Source evaluated: headroomlabs-ai/headroom (Apache-2.0).

Headroom's Python library exposes `compress(messages, ...)` and routes text/JSON/code through content-specific compressors. Evaluation was performed in an isolated temporary virtual environment; Headroom is not yet a project-orchestrator runtime dependency.

## Deterministic corpus
Three seeded agent-evidence classes:
- repetitive GitHub CI failure output with exact first-failure and commit markers;
- unattended Product Reality worker output with exact failure and head markers;
- structured PR/check evidence with exact PR/head/mergeability markers.

The benchmark fails closed on loss of any essential marker. Token counts produced by the Daedalus harness are explicitly estimates; Headroom-native tokenizer counts are recorded separately.

## Result — 2026-09-19
Overall deterministic estimate: 21,795 -> 3,429 tokens-equivalent, remaining ratio 0.1573, essential signal retention 1.0000.

Headroom-native measurements:
- CI failure: 6,186 -> 124 tokens; 6,062 saved; essential markers retained.
- Product Reality worker: 7,275 -> 95 tokens; 7,180 saved; essential markers retained.
- Structured PR evidence: 6,877 -> 4,080 tokens; 2,797 saved; essential markers retained.
- Measured compression runtime for all three cases: ~817 ms total on Azathoth, including first-call initialization.

Disposition: PILOT_READY, not production-integrated. The next gate is a live-evidence pilot on real Daedalus GitHub/worker payloads with identical diagnostic decisions before and after compression.
