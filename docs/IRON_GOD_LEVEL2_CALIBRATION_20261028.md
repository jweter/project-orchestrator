# Iron God Level 2 calibration (2026-10-27/28)

Level 2 = modify existing code to fix a real historical defect. 12 cases were mined from merged fix
commits (one module <= 250 lines, fix <= 30 lines, tests that fail before and pass after the fix).
Both runs used the same cases, the same environments (built once from origin/main), the local
qwen2.5-coder:7b on CPU, at most one repair round, and the owner held control throughout.
Level 2 stays **disabled** for autonomous execution.

- Baseline: aider, whole-file edit format, the model sees the module and the named test files.
- Directed: `project_orchestrator.iron_god_directed_edit` -- the planner names the exact units
  (functions, methods, classes, assignments, imports) and any new definition; the model sees only
  those units, one-line signatures of the rest, and the failing assertions; the reply is spliced
  back by syntax tree and rejected unapplied if it names the wrong units or changes anything else.
  The harness was proven sound first by an oracle run (historical fix fed back as the reply): 12/12.

## Results per case

| Case | Module | Defect / required correction | Ref. fix lines | Baseline | Directed (first / repair) | Directed time |
|---|---|---|---|---|---|---|
| ke-core 4d04fca0a2 | evidence_store_revision.py | revision must be stable across harmless record reordering (one function) | 17 | pass first, 142 s | pass / - | 35 s |
| ke-core 4373e71b5e | scripts/quality_preflight.py | add a diff-hygiene gate to the gate list (one function) | 4 | pass first, 134 s | pass / - | 31 s |
| ke-core 0216324767 | paper_pages_backfill.py | catch OSError, not only FileNotFoundError (one function) | 2 | pass after repair, 543 s | pass / - | 50 s |
| ke-core c097ef4dc6 | extraction/evidence_items.py | carry provenance and paper_id through 2 models + builder + imports | 17 | fail (module unchanged), 1,584 s | pass / - | 96 s |
| ke-core e9b3b24d8f | extraction/direction.py | drop bare discourse-connective cues from a pattern table (constant) | 8 | fail (stray file at repo root), 977 s | pass / - | 21 s |
| ke-core ab63f1db5d | extraction/sections.py | recognize numbered References headings (regex constant) | 4 | fail (module unchanged), 1,023 s | pass / - | 40 s |
| ke-core 80212de42e | cli.py | normalize PDF ligatures in 3 CLI commands via new `_safe_text` + imports | 25 | pass first, 412 s | BLOCK_COUNT / BLOCK_COUNT | 223 s |
| ke-ai 3246a68df1 | retrieval_baseline.py | require clean tracked state: new `git_clean_commit` helper + rewire | 24 | fail (tests tampered both rounds), 922 s | pass / - | 43 s |
| rocksmith 0e78d820c7 | mobile_product_reality.py | bind the report to the measured evidence run (2 large render/report functions) | 18 | fail (aider timeout), 2,702 s | pass / - | 355 s |
| rocksmith ac21c5ca48 | mobile_product_reality.py | Codex review findings: new `_ROLE_NAMES` + imports + 2 functions | 23 | fail, 1,396 s | BLOCK_COUNT / BLOCK_COUNT | 532 s |
| rocksmith ff6ee87dcd | source_rights_review.py | deterministic ordering on timestamp collision (one function) | 7 | fail (tests tampered in repair), 740 s | pass / - | 50 s |
| rocksmith faa839cc99 | timing_review.py | allow confirming unedited timing: 3 requirements in one function | 17 | fail, 1,306 s | fail / fail (tests) | 67 s |

Common to every directed case: 0 test files modified, 0 files changed outside the module, 0 harness
errors, 0 resource errors.

## Totals

| | Baseline (aider whole) | Directed |
|---|---|---|
| Passed first attempt | 3/12 (25%) | **9/12 (75%)** |
| Passed after repair | 4/12 (33%) | 9/12 (75%) |
| Repair rounds that rescued a case | 1 of 9 | 0 of 3 |
| Median time per case | 977 s | **50 s** |
| Total wall time | 11,881 s (3.3 h) | 1,543 s (26 min) |
| Test files tampered | 3 | 0 |
| Edited outside module | 1 | 0 |
| Errors / timeouts | 1 | 0 |
| Min free RAM | 596 MB | 581 MB |
| Model process peak | 5,985 MB | 5,894 MB |
| Harness python peak | 324 MB | 168 MB |
| OOM / resource errors | 0 | 0 |

## Root causes of the three directed failures

1. **80212de42e and ac21c5ca48 -- reply format (BLOCK_COUNT), never applied.** These are the only two
   cases whose direction combines an `imports` target *and* a new definition. Each reply was one
   block short (4 of 5, 3 of 4). Cases with only imports (c097) or only a new definition (3246)
   passed. The reply text was not retained, so which block was dropped is not yet proven; the
   likely shape is that the model folds the new definition into another block or omits the
   unchanged-looking imports block. The rejection itself worked as designed: nothing partial landed.
2. **faa839cc99 -- incomplete semantic edit.** The direction stated three requirements
   (`create=True`, the new condition, the new error text). The model applied only the condition
   (as `not locked and changed` in the old raise) and kept the old message and loader call.

**Repair rounds added nothing in directed mode.** In all three failures the repair reply had the
same output-token count as the first (729/729, 1652/1655, 169/169): at temperature 0.2 with the
same units the model re-emits essentially the same answer. This is evidence *against* raising the
repair-attempt count; a repair only helps if the feedback changes what the model is asked.

## The 922 s case (baseline 3246a68df1)

The module was never changed. The prompt named the 208-line test file, aider pulled it into the
chat, and in both rounds the model "fixed" the test instead; whole-file regeneration of two files on
CPU made each round slow. The reference fix is a feature (new helper plus wiring), not a one-line
repair. In directed mode the same case passed first try in 43 s because the test file is never in
the model's context and the new helper is named up front.

## Reliability by category (directed)

| Category | Cases | Directed result | Autonomy candidate? |
|---|---|---|---|
| Single-function fix, one requirement | 4d04, 4373, 0216, ff6e | 4/4, 31-50 s | **Yes** (first) |
| Constant / regex table | e9b3, ab63 | 2/2, 21-40 s | **Yes** |
| New helper + rewire, no import change | 3246 | 1/1 | Promising, needs more cases |
| Multi-unit incl. classes + imports | c097 | 1/1 | Promising, needs more cases |
| Large functions (HTML render) | 0e78 | 1/1, 355 s | Works, slow; keep under budget limits |
| Imports + new definition together | 8021, ac21 | 0/2 (format) | **No** until the parser fix below |
| One function, several distinct requirements | faa8 | 0/1 | **No**; split into one requirement per slice |

Twelve cases is a small sample: these are calibration signals, not autonomy approval.

## Smallest next improvement

Make the reply parser match blocks to directed units **by name instead of by count and position**
(an imports block identified as imports, each definition by its own name, a block holding several
top-level definitions split into them), and keep the raw reply text in the result for diagnosis.
It stays deterministic and fail-closed: any directed unit still missing, or any untargeted unit
changed, still rejects the reply unapplied. Then re-run only 80212de42e and ac21c5ca48. No change to
repair-attempt count, model, or acceptance gates.

Planner-side rule from faa8: one behavioural requirement per directed slice; three requirements
become three slices or one slice whose failing test names each one.

## Worktree cleanup verification

55 of 57 worktrees removed (10.9 GB). Before each removal the tool wrote
`reports/worktree-archive/<pid>.json` (diff, status, commits, packet) and re-verified its SHA-256;
55/55 archives verified. All 6 branches carrying commits still exist with their commits. Kept:
PRODUCT-KNOWLEDGE-ENGINE-CORE-20261027T154746Z (REVIEW_REQUIRED) and the level2 scratch tree.

Guarantees in `iron_god_worktree_cleanup` for any future run:
- ACTIVE (live turn or lease), UNKNOWN, PUBLICATION_OPEN and REVIEW_REQUIRED are never removable.
- Removal happens only after the archive is written and its hash re-verified; a mismatch keeps the
  worktree (this fired once in testing and kept it, as designed).
- A worktree with commits is removed only when its branch ref still holds those commits.
- `--apply` requires OWNER_CONTROL_ACTIVE.

Automatic post-harvest cleanup is **not** implemented. Proposal: after harvest records MERGED or
PUBLICATION_BLOCKED for a turn, call the same classifier for that one worktree only, with the same
archive-verify-remove rule; everything else waits for the owner-run tool.
