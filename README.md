# project-orchestrator

Portfolio-level control plane for Jeremy Weter's autonomous engineering loop.

This repository is the single portfolio brain above five product repositories:

- Knowledge Engine: `jweter/knowledge-engine-core`, `jweter/knowledge-engine-web`, `jweter/knowledge-engine-ai`
- Rocksmith Song DLC Generator: `jweter/rocksmith-cdlc-generator`
- Everward: `jweter/Project-Everward`

It does **not** own product implementation. Product repositories remain authoritative for roadmaps, architecture, tests, CI, Product Reality, and merge gates. This repository owns portfolio inventory, priority policy, promotion rules, cross-project scheduling state, and evidence-derived compounding measurement.

## Core loop

`observe -> triage -> repair -> rank -> reserve -> preflight -> PR/CI -> merge -> Product Reality -> learn -> measure -> reinvest`

The loop is fail-closed. Missing evidence is `UNKNOWN`; failed or pending gates never become green by inference.

## Local use

```bash
python -m pip install -e ".[dev]"
python engineering/preflight.py
project-orchestrator validate
project-orchestrator plan examples/portfolio-snapshot.json
GITHUB_TOKEN=... project-orchestrator collect --output live-evidence.json
```

See `DoWork.txt`, `engineering/control-plane.json`, `docs/ARCHITECTURE.md`, and `docs/ROADMAP.md`.

## Live evidence

`project-orchestrator collect` reads the canonical inventory and gathers raw live GitHub evidence for all five product repositories. Tokens are read only from `GITHUB_TOKEN` or `GH_TOKEN` and are never written into the output. Check runs are collected but remain explicitly unclassified as required/optional until the required-check discovery layer is applied.
