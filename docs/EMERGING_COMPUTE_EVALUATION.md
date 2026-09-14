# Emerging Compute Evaluation — Quantum-Ready Portfolio Plan

## Purpose

The portfolio should become **quantum-ready without becoming quantum-dependent**. New compute substrates are treated as experimental instruments under the Autonomous Engineering Scientist, not as architecture choices made because a technology is novel.

The governing loop is unchanged:

**Observe → formulate hypothesis → retrieve prior evidence → design bounded experiment → estimate cost → execute → measure → compare → reject or retain → independently verify → record → generalize → repeat.**

For compute evolution, that becomes:

**Verified classical baseline → candidate substrate → same measurement contract → bounded experiment → cost/reproducibility evidence → independent verification → evidence-based adoption or rejection.**

## Multidimensional decision model

Every candidate must be evaluated across seven dimensions.

1. **Problem fit** — Is there a real problem whose structure maps to the proposed method, or are we technology-shopping?
2. **Scientific validity** — Is the mechanism physically/mathematically supported for the claimed use?
3. **Measured advantage** — Does it beat or uniquely complement the best current classical baseline under the same contract?
4. **Reproducibility/noise** — Are seeds, shots, replicates, calibration/environment identity, variance, and repeatability controlled?
5. **Cost** — Compute time, queue time, network calls, external spend, engineering maintenance, and opportunity cost.
6. **Operational complexity** — New SDKs, provider coupling, credentials, hardware queues, simulator drift, vendor lock-in, and support burden.
7. **Project value** — Does the gain materially improve the actual project objective?

A candidate that fails one of these dimensions can be rejected even if it is technically interesting.

## Supported substrate classes

The control plane recognizes:

- `CLASSICAL_CPU`
- `CLASSICAL_GPU`
- `CLASSICAL_ACCELERATOR`
- `QUANTUM_INSPIRED`
- `QUANTUM_SIMULATOR`
- `QUANTUM_HARDWARE`
- `PHOTONIC`
- `NEUROMORPHIC`
- `OTHER_EXPERIMENTAL`

These are execution substrates, not endorsements.

## Deterministic candidate states

A candidate is classified as one of:

- `ELIGIBLE_FOR_BOUNDED_EXPERIMENT`
- `BASELINE_REQUIRED`
- `GATHER_EVIDENCE`
- `SIMULATE_FIRST`
- `APPROVAL_REQUIRED`
- `NOT_JUSTIFIED`
- `FORBIDDEN`

The evaluator grants no execution or merge authority.

## Quantum-specific rules

### Classical baseline first

No non-classical claim is meaningful without a verified classical comparator. The baseline must use the same measurement contract as the candidate.

### Simulator first when applicable

A physical QPU candidate that can be meaningfully exercised in simulation must pass a simulator experiment before paid hardware is considered. This avoids spending money to discover mapping, circuit, or measurement mistakes that simulation could have caught.

Simulation is not treated as proof of physical hardware performance. It is a lower-cost experimental gate.

### Stochastic evidence is first-class

Quantum and many emerging-compute methods are stochastic or hardware-state-dependent. The experiment record must preserve the relevant reproducibility information: seeds, shots/replicates, backend/environment identity, calibration/noise context when material, and repeatability evidence.

### Paid hardware is approval-bound

The default autonomous external-spend allowance is zero dollars. Physical hardware or paid cloud execution remains behind the Agent Action Guard unless policy is explicitly changed.

### Do not misuse “quantum advantage”

A local benchmark can establish that a candidate performed better than the measured classical baseline for a bounded workload. It does **not** by itself establish general quantum advantage, asymptotic speedup, or superiority over the best known classical algorithm.

The control plane therefore uses `BENCHMARK_SUPERIORITY_VERIFIED`, not “quantum advantage,” for local experimental success.

## Implementation phases

### Phase 0 — Governance and substrate neutrality — implemented

- Add a deterministic emerging-compute evaluator to the Scientist.
- Add machine-readable policy.
- Require a verified classical baseline and one measurement contract.
- Keep runtime product dependencies unchanged.
- Make broad advantage claims fail closed.

### Phase 1 — Project applicability maps — implement now

Each product gets its own stance rather than inheriting a blanket “quantum is useful” assumption.

- **Knowledge Engine:** HIGH strategic research fit. Prepare hybrid-compute research and quantum-claim validation contracts across Core/Web/AI.
- **Everward:** MEDIUM/HIGH scientific-worldbuilding fit. Use real quantum sensing, timing, materials, communication constraints, and future computation only where scientifically defensible and gameplay-relevant.
- **Rocksmith:** LOW current fit. Reject quantum work unless a measured classical bottleneck plus credible problem mapping emerges.

### Phase 2 — Free/local experimentation first

When a qualifying problem exists:

1. freeze the classical baseline;
2. write the measurement contract;
3. implement the lowest-cost candidate, normally quantum-inspired or simulator-based;
4. run repeated measurements;
5. compare accuracy/correctness, runtime, variance, cost, and maintenance complexity;
6. independently verify the result;
7. reject or retain the hypothesis.

No physical QPU is required for this phase.

### Phase 3 — Vendor-neutral adapter contract

Only after at least one simulator experiment is useful enough to repeat, define a provider-neutral adapter around:

- backend class and identity;
- qubit/resource count where applicable;
- circuit/problem depth/size;
- shots/replicates;
- queue time;
- execution time;
- calibration/noise metadata;
- cost;
- result distribution;
- confidence/variance;
- provenance and evidence references.

Do not let product code depend directly on one QPU provider SDK.

### Phase 4 — Physical hardware experiments

A real QPU experiment is eligible only when:

- the project problem remains valuable;
- the classical baseline is still current;
- simulator or pre-hardware evidence justifies the run when applicable;
- expected cost is known;
- Agent Action Guard grants approval;
- the Resource Scheduler has capacity;
- exact environment/backend identity can be recorded;
- independent verification strategy is defined before execution.

### Phase 5 — Cross-repo distillation

A successful compute pattern remains local until at least two independent repositories/projects verify a domain-independent lesson. Examples might include:

- simulator-first cost control;
- stochastic-result provenance;
- backend identity as part of exact environment evidence;
- repeated-measurement contracts;
- cost-normalized hypothesis comparison.

Product-specific quantum requirements never transfer automatically.

### Phase 6 — Dedicated Quantum Engineering Lab activation

Do **not** create a separate lab repo because quantum computing is interesting.

Create it only when all of the following are true:

1. at least one real portfolio problem has a verified classical baseline;
2. a quantum/quantum-inspired/simulator candidate has survived the Scientist gates;
3. the experiment is useful enough to warrant repeated work rather than a one-off proof;
4. a reusable adapter/benchmark/provenance layer would reduce duplication across projects;
5. the Resource Scheduler can bound its compute/cost footprint;
6. there is a clear first milestone that can fail and still produce reusable evidence.

The lab then becomes a shared experimentation project, not a technology showcase.

## Project-specific first experiments

### Knowledge Engine

Potential future experiments, in priority order:

1. **Quantum-science evidence quality:** teach research workflows to distinguish theoretical, simulated, hardware-run, and independently reproduced claims.
2. **Hybrid optimization benchmark:** choose a real bounded optimization problem from Knowledge Engine and compare the current classical solution with a quantum-inspired or simulator candidate.
3. **Quantum chemistry / scientific-computing research support:** only after the evidence/provenance layer can capture Hamiltonian/model assumptions, backend conditions, and comparison baselines.
4. **QPU execution:** only if a simulator candidate remains scientifically and economically interesting.

### Everward

Use quantum physics first as scientifically grounded capability design:

- precision clocks and synchronization;
- interferometric/quantum sensing;
- magnetometry and gravimetry;
- quantum materials and nanoscale effects where relevant;
- secure quantum communication concepts with classical-channel constraints;
- onboard quantum computation only if it improves a defined probe decision problem.

Hard canon boundaries:

- entanglement does not provide faster-than-light communication;
- quantum teleportation does not teleport macroscopic matter;
- “observer” is not shorthand for human consciousness creating reality;
- gameplay abstractions must preserve the underlying physical constraint even when simplified.

### Rocksmith

Current default: **do not pursue quantum implementation**.

A future candidate must first demonstrate:

- a measured classical bottleneck in alignment, transcription, optimization, or search;
- a credible mapping to a quantum/quantum-inspired method;
- an inexpensive simulator or analytical experiment;
- superiority under the exact same musical-quality/timing contract;
- enough gain to justify added complexity.

Absent those conditions, the correct Scientist outcome is `NOT_JUSTIFIED`.

## Success metrics

The portfolio should measure outcomes rather than novelty:

- percentage of emerging-compute proposals rejected before implementation because problem fit is weak;
- experiments with verified classical baselines;
- experiments sharing an identical measurement contract across substrates;
- cost per useful result;
- reproducibility rate across repeated runs;
- independently verified benchmark improvements;
- vendor-specific dependencies avoided;
- human approvals consumed for paid experiments;
- transferable patterns earned through Cross-Repo Pattern Distillation.

A high rejection rate can be healthy if it prevents expensive technology-driven churn.
