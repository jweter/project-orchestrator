# Iron God Pocket — New Mobile Project Charter

**Planning status:** proposed separate iOS product track; **not** a current executable iOS app, installed service, or owner runtime-mode change. 2026-11-01.

**Parent project:** [Iron God / project-orchestrator](https://github.com/jweter/project-orchestrator)  
**Product form:** Offline development cockpit + optional authenticated Azathoth control  
**Project ID:** `iron_god_pocket`

## Vision and product promise

Create a trustworthy iPhone-native engineering cockpit that remains useful with no laptop or network: inspect saved verified development evidence, triage a local backlog, draft owner-reviewed roadmap proposals and review diffs/receipts. When Azathoth is reachable, optionally become a securely authenticated remote status and owner-control client without becoming a competing autonomous controller.

## Current verified foundation

The parent repository already ships `dashboard/index.html`, `dashboard/app.js`, `dashboard/manifest.webmanifest`, and `dashboard/sw.js`, including phone-first viewport, status, worker telemetry, Home Screen display, and powerful mode/recovery/reboot controls. Those are an existing PWA starting point, **not** a standalone native build and not proof that those control routes are safe on iOS.

## First real user journey

In airplane mode, open Iron God Pocket, review last imported signed or digest-verified engine evidence bundle, inspect blocked slices and CI history as snapshots, add a prioritized local engineering note, export it as a draft handoff file. With connectivity explicitly enabled, inspect the live engine and, after strong authentication and owner-confirmation, submit a bounded control request and receive a verifiable receipt.

## Non-negotiable standalone contract

- Read-only, date-stamped cache of previously imported project state; clearly label snapshots STALE/OFFLINE and never present them as live health.
- Local portfolio/roadmap notes, prioritized issue candidates, dependency view, PR/commit/diff review from manually imported Git/evidence bundles, and export to Files.
- On-device deterministic diff, JSON-schema validation and document search; optional small local summarizer only after safety/performance validation.
- No claims of continuously running git CI workers, GitHub merges, Windows remote control or Azathoth autonomous execution on an offline iPhone.

## Optional connected expansion (not a dependency)

Read-only Tailscale/local HTTPS status and explicit authenticated control with short-lived scoped credentials, biometric reauthentication, confirmation for mutating actions, anti-replay receipt, and a clearly visible remote/offline mode. Never persist an unrestricted admin bearer token in web localStorage as a native architecture. No direct public Internet exposure.

## Out of scope and honest feasibility boundary

Actual remote control and workers depend on Azathoth/GitHub being online. A *fully standalone iPhone engineering cockpit* is realistic; a *fully autonomous Iron God Windows worker* is not the v1 iOS objective.

## Portfolio operating principles

- Phone-first: app launches and performs its declared MVP value in airplane mode without Azathoth, GitHub, a hosted API, or a paid service.
- Native-first for the long run; a PWA or companion prototype may validate UX sooner, but may not be represented as proof of fully native offline functionality.
- One source of product truth: share formats, algorithms and fixtures with parent project; never fork the entire Python/Windows backend or invent duplicate business rules.
- Optional sync must be explicit, authenticated, revocable and resilient to disconnection; an offline session must never trigger an implicit network request.
- Protect user data: use iOS sandbox, Files integration, file protection/Keychain for secrets; avoid collecting unnecessary user content and telemetry.
- Keep parent project quality policies: narrow branches, exact-head tests, licensing/provenance checks, review and product reality. Do not install or alter the running Iron God release.
- Measure device performance, memory, thermals and battery on physical supported iPhones; record actual evidence before making capability or compatibility claims.
- Maintain accessibility (VoiceOver, Dynamic Type where applicable, contrast, touch targets), cancel/resume behavior and storage bounds.
- No Apple membership purchase, Mac rental, new paid service, signing certificate or public App Store distribution without separate explicit owner decision.

## Build environment and cost constraints

Native iOS signing, on-device testing and App Store submission normally require macOS/Xcode infrastructure; the Windows laptop can remain the source-development and test workstation. Start with portable C++/Swift contract fixtures and SwiftUI design prototypes where a Mac runner/device is not yet available. Personal device development can start without paid Apple Developer Program membership, subject to Apple's provisioning limits; App Store/TestFlight distribution requires program enrollment.

Default cost target $0 before owner-approved developer membership, build-machine access or cloud usage. Keep optional model downloads, paid APIs, hosted inference and other recurring costs opt-in. No automatic spending.

## Parent-code and platform references

- https://github.com/jweter/project-orchestrator/blob/main/dashboard/index.html
- https://github.com/jweter/project-orchestrator/blob/main/dashboard/app.js
- https://github.com/jweter/project-orchestrator/issues/597


---

# Iron God Pocket — Phased iPhone Roadmap

**Status:** PLANNED. Phases are evidence gates, **not calendar commitments**. Parent projects retain authority over their existing implementation and product quality.

## Release strategy

Start by proving a no-server/no-laptop local user journey. Give native/local capabilities their own implementation and acceptance tests. Add external integrations only after the standalone core works. For every phase: maintain a narrow branch, exact-head tests, real device evidence and explicit limitations; do not call draft prototypes complete.

## P0 — Existing dashboard audit and mobile contract

**Deliverables**

- Audit dashboard PWA and /api control/read-only contracts; inventory the actual authenticated owner-control boundary and sensitive commands.
- Define portable versioned snapshot/evidence export schema (source repo/SHA, observation time, provenance, integrity hash, gate state).
- Prototype phone navigation and negative/offline data states; identify what can be safely reused verbatim.

**Exit gate:** A fixture-backed schema and threat model exist; static PWA passes a phone-size review; no remotely mutating code added.

## P1 — True standalone offline viewer

**Deliverables**

- Create SwiftUI app shell; import a sample Iron God snapshot via Files; save/read/delete it locally.
- Show repositories, active/saved packets, merge evidence, blocked reason, accepted-local vs released SHA and age-of-data warnings.
- Add on-device diff/receipt inspector, search and export; disable all network capability in this milestone.

**Exit gate:** In airplane mode with laptop off, a real device imports and explores an evidence packet, edits planning notes and exports them after force-quit/relaunch.

## P2 — Pocket planner and working-memory companion

**Deliverables**

- Add objective/slice/dependency editor using owner-approved roadmap annotations without granting execution authority.
- Validate planned draft files against strict backlog/admission contract with read-only local fixture tests.
- Add conflict/duplicate warnings and audit log for local draft edits; export handoff bundle, never auto-run it.

**Exit gate:** A schema-valid draft package moves between iPhone and desktop in a test fixture; no autonomous runtime state was silently changed.

## P3 — Optional secure remote mode

**Deliverables**

- Introduce explicit connect/disconnect to existing engine over authenticated private transport; status first.
- Test owner-mode and high-impact remote controls with server-side authorization, confirmation, idempotent request IDs and signed/fenced receipts.
- Prove disconnect/expired credential/Take the Wheel/replay failure cannot silently execute actions.

**Exit gate:** Real iPhone can monitor safely while disconnected and reconnect without false claims; sensitive actions obey existing owner controller.

## P4 — Native beta and safety review

**Deliverables**

- Validate accessibility, data protection, remote-control threat model and migration of imported schema versions.
- Profile offline performance, repeat crash/reopen, connectivity oscillations and physical iPhone UI.
- Decide whether this should remain a standalone app or incorporate an opt-in hosted read-only sync service.

**Exit gate:** No regression in the original on-laptop controller; zero required services for offline functions; signed iPhone beta accepted.

## First independently actionable engineering slices

1. IGP-001: inventory dashboard PWA endpoints, current auth and existing mobile UX
2. IGP-002: design stable export-only local snapshot schema with exact SHA and age
3. IGP-003: implement offline iPhone viewer fixture with zero networking
4. IGP-004: draft read-only/local roadmap planner notes and export
5. IGP-005: define optional owner-authorized remote control security contract

## External blockers / decisions held for later

Actual remote control and workers depend on Azathoth/GitHub being online. A *fully standalone iPhone engineering cockpit* is realistic; a *fully autonomous Iron God Windows worker* is not the v1 iOS objective.

Open decisions to defer: name/icon/branding; minimum iOS version and target devices after a build smoke; distribution model and monetization; Apple signing infrastructure; optional online sync; optional on-device inference models. None block offline planning and portable-contract work.

## Change-control and roadmap hygiene

This iOS roadmap is a distinct **proposal** and does not silently expand the owner-approved desktop implementation roadmap. On approval of a phase, add narrowly bounded capability graph entries or planning issues in the appropriate development project; do not auto-enroll these proposals in the active Iron God production dispatcher, grant runtime execution rights, or compete with its owner/session control. Cross-repo dependencies must reference the specific parent schema/commit and explicit acceptance evidence. Record verified milestone outcomes, not guesses.


---

# Iron God Pocket — Architecture, Risks and Validation

## System components and reuse seams

- Native `PocketUI` (SwiftUI), `LocalEvidenceStore` (SQLite/SwiftData plus file hashes), `OfflinePlannerNotes`, `DiffReview`, `EvidenceImportExport` using Files.
- `EngineProtocolAdapter` maps existing product-lane JSON snapshot, packet state, CI/review gates, local accepted vs released SHA, and ref/evidence provenance without altering the runtime schema.
- `OptionalRemoteClient` behind a feature gate, using authenticated, scoped request/receipt contract. High-impact controls may never execute while device is offline.
- Risk and owner authority boundary isolated from offline planner suggestions: a phone note is never itself a worker authorization or approved release.

## Offline / optional-online data flow

**Offline:** Files / mic / touch input → local validated parser → versioned local store → deterministic logic and (if proven) on-device inference → local UI / Files export. No remote call is necessary for the core workflow.

**Online enhancement:** explicit user action → permission/identity check → versioned optional adapter → external backend or online public source → source/receipt validation → local persisted result. Network failure leaves the offline product functional; remote-only operations fail visibly and never impersonate local results.

## Technical and delivery risks

- **Unattended development on iOS:** The desktop Iron God supervisor is not an appropriate native continuously running iOS background service; plan a separate foreground-only experimental tool, not a promised phone replacement.
- **Privilege boundary:** Existing dashboard includes mode, recovery and reboot endpoints; require threat review, scoped tokens and explicit owner verification before exposing native controls.
- **Stale snapshots:** Separate observation timestamp, accepted development state and actual released GitHub state; do not let offline caching imply current readiness.
- **Competing state stores:** Use imports/proposals first, later a single authenticated API; never write protected SQLite/ref stores directly from mobile.

## Cross-cutting real-device acceptance matrix

1. Fresh install + airplane mode + Azathoth completely off: app launches and completes the documented core user journey without stalls or hidden remote services.
2. Cold start, memory peak, battery/thermal behavior, model/download size and task latency measured on an actual physical device. Set release budgets from the first spike rather than inventing benchmark successes.
3. No unauthenticated inbound listening port, leaked token, bundled source secret, plaintext private sync credential or surprise upload; perform negative network test.
4. App returns a visible failure state when inputs, permissions, local model assets or Files permissions are missing. No fabricated output or false completion.
5. User can export, back up and delete locally created work without a server; app survives force quit and offline relaunch without losing completed edits.
6. Cross-platform contract/fixture tests verify imported/exported formats against the parent repository; snapshots are not treated as live or authoritative.
7. Standard automated checks, independent review, privacy/permissions declarations, App Store licensing/provenance review and actual iPhone product test must pass before external beta.

## Release evidence template

- Exact source head and build identity; Xcode/SDK/device/OS and model/runtime version if used.
- Recorded airplane-mode test with desktop/server powered off, input fixtures, expected outputs, pass/fail and timings.
- Memory/CPU/battery/thermal/storage measurements; accessible UI screen review; interruption/resume evidence.
- Data privacy/security review, license inventory, signed-build provenance, reviewer approval and open blocker list.
- Explicit distinction: proposed concept vs prototype vs device-proven vs App Store distributable.

## References

- https://github.com/jweter/project-orchestrator/blob/main/dashboard/index.html
- https://github.com/jweter/project-orchestrator/blob/main/dashboard/app.js
- https://github.com/jweter/project-orchestrator/issues/597
