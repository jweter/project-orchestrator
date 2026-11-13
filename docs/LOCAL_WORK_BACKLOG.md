# Local work backlog: developing code when the capable builder is spent

The capable builder has a rolling daily budget (6 dispatches in 24 hours, owner-approved; never raised by this document or its
tooling). When it is spent the engine can still develop code with the free local model, but only inside what that model is
measured for. This document is the contract for keeping that lane fed, so a spent budget means smaller work, not no work.

## What the local builder can do (measured, not hoped)

Measured on the owner's laptop (CPU only, `qwen2.5-coder:7b`): a decent **builder** and a weak **planner**.

- **Level 1**: one new pure function in a new module, with literal acceptance examples. It builds a ~60-line module plus tests
  in a few minutes; about one turn in five passed end to end before the guards below, because it invented contradictory
  examples, named functions that do not exist, or specified a function against a file that cannot exist.
- **Level 2**: a directed edit of one to three named units of an existing module, against an approved test that fails at the
  base for the right reason.
- Anything above Level 2, anything in a language the local lane does not build (the Unreal C++ and Electron UI work), and
  anything that needs private data or a real song goes to the capable builder or a person.

## The rule that makes it work: examples written by a stronger agent

The planner's weakness is deciding what is correct. So a local-sized slice carries its own **independent** acceptance
examples, written by a stronger agent (Claude Code, ChatGPT, or the capable builder) and committed in the capability graph.
The engine then routes it LOCAL and the 7B only writes the code that satisfies examples it did not invent.

### Level 1 slice template

```json
{
  "id": "short-kebab-id",
  "state": "PLANNED",
  "capability_level": 1,
  "depends_on": [],
  "objective": "One paragraph, at most 1200 characters: the roadmap outcome it serves, then exactly what the one pure helper does, including edge rules (rounding, empty input, case). No file access.",
  "acceptance": ["the helper is a pure function in a new module under <package>/ with a type-annotated signature and a short docstring", "a test file covers the literal examples exactly", "no existing module is edited"],
  "acceptance_examples": [
    {"call": "{fn}(1.5)", "expected": 1500, "why": "one sentence saying why this is the right answer"},
    {"call": "{fn}(0.0)", "expected": 0, "why": "..."}
  ],
  "allowed_paths": ["<package>/**", "tests/**"],
  "forbidden_paths": [".github/**", "private/**", ".env"],
  "runner_requirements": ["code", "local", "offline"],
  "runtime_requirements": []
}
```

Rules, each one learned from a failure:

1. **Every example is a literal call** `{fn}(...)` with literal arguments, a literal `expected`, and a non-empty `why`. At
   least two; cover the edges (empty, zero, case, duplicates). Do not give the same call two different answers.
2. **Pure means no files.** Never a `path/to/...` placeholder, a media or archive filename passed to a path parameter, or a
   `Path`/stream parameter. If the real function reads a file, make the slice take the data itself, or send the slice to the
   capable builder.
3. **State every edge rule in the objective** (what an empty list returns, whether matching ignores case, what "within
   tolerance" means at the boundary). The 7B guesses anything left open.
4. **New module only, in an existing package.** `allowed_paths` must hold a code pattern such as `pkg/**`, not only tests,
   docs or json. Do not ask it to wire the helper anywhere; wiring is a separate slice.
5. **Each text field at most 1200 characters, and a Level 1 objective at most 580.** The builder is shown the objective's
   first 580 characters and nothing after them, so the validator refuses a longer one. **State the rule in the first
   sentence** ("Return ... ; X is true only when ...") and put background (issue numbers, documents, history) last or in an
   example's `why`. Measured 2026-11-13: an objective that opened with background lost its rule at the old 160-character cut,
   the 7B guessed what "symmetric" meant, and 3 of 5 correct examples failed. Correct examples do not rescue a missing rule:
   the 7B does not reliably infer a rule from examples or from failing tests.
6. **It must serve a stated roadmap outcome.** Say which one in the objective. Small slices that exist only to keep the lane
   busy are not allowed: the engine's own rule is that repeated adjacent activity is evidence to redesign, not to add more.
7. One slice per capability runs at a time (the engine's contract), so chain related slices with `depends_on` and keep each
   capability's slices in the order they should merge.

### Level 2 slice

Set `capability_level` to 2 and give a complete `directed_edit` (`module_path`, `test_path` for a **new** test file,
`test_source`, `targets`). The test must fail at the base for the right reason, which you can prove by running it.

## Check it before you commit it

```text
python scripts/iron_god_validate_local_slice.py --graph engineering/capability-graph.json \
    --repository jweter/<name> --slice <slice-id> --state-root "%LOCALAPPDATA%\ExponentialEngine\flow-state"
```

It applies the engine's own rules (example shape, contradictions, placeholder paths, text bounds, a place for a new module,
a complete directed edit) and the measured-capability routing, and prints `PASS` or each `PROBLEM`. A `note` saying "not
executable right now" is not a failure: an earlier slice or a dependency comes first.

## Why a slice used to wait even when the local lane was idle, and the lookahead

The compiler yields work for **one capability per repository at a time, in roadmap order**. If the slice at the head of the
line needed the capable builder (above Level 2, or its budget was spent), the repository offered the local builder nothing,
however many small slices sat behind it. `iron_god_roadmap_refill.local_lookahead` fixes that:

- It runs only when the head of the line routes to someone other than the local builder.
- It sets the capabilities already offered behind the rest of the roadmap (none is dropped; the graph contract requires the
  roadmap to list every unfinished capability), asks the compiler again, and keeps only requests that route LOCAL.
- It looks at most `LOCAL_LOOKAHEAD_CAPABILITIES` (4) capabilities down, never offers a capability whose dependencies are
  unfinished, never reorders the roadmap on disk, and never changes a gate. A failure leaves the old behaviour untouched.
- Each offered slice is recorded as a `LOCAL_LOOKAHEAD` ledger event.

It takes effect only after the owner stages and promotes a release; until then the old head-of-line behaviour stands.

## Who replenishes it

The engine cannot write good examples for itself, so replenishing is a scaffolding job for whichever stronger agent is
available. When a repository has no LOCAL-routable slice and its capable budget is spent, that repository is out of local work:
author a few more slices to this contract (Claude Code, ChatGPT, or the capable builder during its next session), validate
them with the script above, and merge them as an ordinary graph pull request with the repository's own gates. The
`docs/project-status.yaml` continuity file of the repository changes in the same pull request when the next slice changes.

## What this does not claim

Local-sized slices are small pure helpers and small edits. They keep development moving; they do not replace the capable
builder for large slices, they have not been run end to end on the engine since the lookahead was written (evidence state
CODE_MERGED once merged, never RELEASE_DEPLOYED until promoted), and the 7B still fails some of them. A failed local attempt is
bounded by the existing attempt limits and returns the slice to the capable builder.
