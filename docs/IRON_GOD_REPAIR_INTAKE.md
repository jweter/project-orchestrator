# Iron God repair-outline intake — Issue #710 next slice

This module converts a **completed local Ollama repair advisory** (#719) to an
inspectable **proposed** item matching the existing Iron God backlog schema.
It does not add an item to that backlog, release a worker, grant an attempt or
make the exhausted child eligible again.

## Review-only intake

Once released through the separate owner-controlled process, the one-shot
entrypoint is:

    python scripts/iron_god_repair_intake.py --lane <lane> --release-repo <pinned-release-repository> --clone <product-repository-clone> --advice-sha <64-hex-digest> --repository <owner/repo> --head <40-hex-Git-HEAD>

Requirements: existing owner Take the Wheel acknowledged by the live
supervisor for the accepted pinned release, exact local clone Git HEAD,
valid installed backlog without rejected entries, trustworthy canonical lane
backlog ledger, current BLOCKED parent plan and EXHAUSTED child with matching
attempt/outcome/packet evidence, unchanged checkpoint, and independently
validated #718 repair outline.

The output is written **only** under
<lane>/microtask-repair-intake/<advice-sha>.json. It contains a valid
backlog-shaped **status=proposed** item and a checklist for independent review.
The item's synthetic REPAIR_REVIEW: roadmap reference cannot match the
existing verified-owner-roadmap auto-promotion rule.

Changing the item's status to approved is insufficient: it deliberately
lacks the required Level 1 build spec or Level 2 directed edit with a
real owner-reviewed red test. A candidate cannot import or dispatch itself,
even if copied verbatim into the canonical backlog file. The original failed
attempt lineage and verified sibling work remain untouched.

If a live approved work item already owns the target module, intake fails
rather than create a conflicting repair proposal. Repeated identical source
evidence is idempotent; a corrupt existing review record or an unclaimed lock
blocks rather than silently recreating or replacing it.

## Separate next slice

A later authorization-aware integration must take an independently reviewed
test/build plan through **the existing owner-approved roadmap and backlog
admission**, preserving original lineage caps, target ownership, lease and
preflight/CI/merge gates. This code deliberately leaves that step unimplemented.

Real three-edit local Ollama development, interruption/restart recovery,
cross-repository fairness, full product acceptance and zero-cloud proof are
not claimed.
