# Project Deep Seam

Project Deep Seam is the portfolio capability-authority and automatic work-replenishment initiative for Project Azathoth Foundry.

## Objective

Allow each governed product repository to declare explicit, machine-readable capability authority so Foundry can continuously select the next safe executable slice without inventing product direction or requiring a human to inject each task.

## Core invariant

**Authority before autonomy.** A worker may mutate a product only when its exact activated HEAD contains a valid capability graph declared by that repository's control plane. Missing, stale, malformed, conflicting, or non-executable authority fails closed.

## End-to-end contract

1. Repository control plane declares an authoritative capability graph.
2. Capability compiler reads that graph from the exact activated HEAD.
3. Validation proves roadmap state, dependencies, acceptance evidence, and completion state are internally consistent.
4. Slice derivation produces bounded executable work with explicit acceptance criteria and path authority.
5. Planner selects only conflict-free authorized slices.
6. Dispatcher grants a compatible runner a bounded lease.
7. Verification and Product Reality gates determine promotion.
8. Fresh merged evidence advances capability state and replenishes the queue.

## Portfolio rollout

Deep Seam is complete only when all governed product repositories have exact-head authority and can replenish safe work:

- `jweter/VoiceStudio`
- `jweter/knowledge-engine-ai`
- `jweter/knowledge-engine-core`
- `jweter/knowledge-engine-web`
- `jweter/Project-Everward`
- `jweter/rocksmith-cdlc-generator`

VoiceStudio is the first proving repository because its S1 singing capability is already in verification and its next authoritative milestone is S2, Singing project/input data model.

## Safety properties

- Never derive mutation authority from arbitrary roadmap prose at runtime.
- Never treat tests alone as Product Reality.
- Never broaden allowed paths beyond the declared slice.
- Never convert missing or ambiguous evidence into PASS.
- Never advance capability state from an unmerged or mismatched HEAD.
- Preserve repository-local security, licensing, consent, provenance, and architecture gates.
- Healthy active workers are not interrupted merely to deploy control-plane changes.
