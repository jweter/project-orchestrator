# External Capability Acquisition

Daedalus treats public engineering systems as evidence sources, not automatic dependencies.

Pipeline: DISCOVER -> CLASSIFY -> LICENSE CHECK -> ARCHITECTURE EXTRACT -> CAPABILITY MAP -> OVERLAP ANALYSIS -> GAP ANALYSIS -> BENCHMARK -> ADOPT/ADAPT/REJECT -> RECORD.

## Safety and promotion rules
- Never auto-install or execute third-party code during discovery.
- Record repository, license, upstream evidence, dependency risk, and target product.
- Existing verified capabilities bias toward ADAPT rather than duplicate dependencies.
- Non-permissive/unknown licensing is benchmark-only until explicitly cleared.
- INTEGRATE is a hypothesis until a repo-native benchmark demonstrates measurable value.
- Every adopted capability needs regression protection and a rollback path.

Wave 1 prioritizes OpenSpec, Spec Kit, Hermes Agent, Headroom, Docling, and PageIndex. Scrapling, mem0, Fabric, and AI Engineering Hub follow as bounded evaluations.
