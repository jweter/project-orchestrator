from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
REGISTRY = ROOT / "engineering" / "control-surface.toml"
ENGINEERING = ROOT / "engineering"
DOCS = ROOT / "docs"

EXPECTED_SCRIPT = {
    "project-orchestrator": "project_orchestrator.cli:main",
}
MAX_AUTHORITY_JSON = 30
JSON_CATEGORIES = ("authority", "schema", "example", "fixture", "authoritative_state")
LEGACY_EXECUTABLES = (
    "project-orchestrator-throughput",
    "project-orchestrator-evolve",
    "project-orchestrator-scientist",
    "project-orchestrator-kpi",
    "project-orchestrator-loop-audit",
    "project-orchestrator-harness-audit",
    "project-orchestrator-harness-security",
    "project-orchestrator-agent-eval",
    "project-orchestrator-agent-eval-runner",
    "project-orchestrator-skill-extract",
    "project-orchestrator-learning-candidate",
    "project-orchestrator-handoff-memory",
)


def validate() -> list[str]:
    errors: list[str] = []

    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    scripts = project.get("project", {}).get("scripts", {})
    if scripts != EXPECTED_SCRIPT:
        errors.append(
            f"installed CLI surface must be exactly {EXPECTED_SCRIPT!r}; got {scripts!r}"
        )

    registry = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    if registry.get("canonical_cli") != "project-orchestrator":
        errors.append("control-surface registry canonical_cli mismatch")

    categories = registry.get("json", {})
    classified: dict[str, str] = {}
    for category in JSON_CATEGORIES:
        entries = categories.get(category)
        if not isinstance(entries, list):
            errors.append(f"registry category missing or invalid: {category}")
            continue
        for name in entries:
            if not isinstance(name, str) or not name.endswith(".json"):
                errors.append(f"invalid JSON registry entry in {category}: {name!r}")
                continue
            if name in classified:
                errors.append(
                    f"JSON classified more than once: {name} "
                    f"({classified[name]}, {category})"
                )
            classified[name] = category

    actual = {path.relative_to(ENGINEERING).as_posix() for path in ENGINEERING.rglob("*.json")}
    registered = set(classified)
    for name in sorted(actual - registered):
        errors.append(f"unclassified engineering JSON: {name}")
    for name in sorted(registered - actual):
        errors.append(f"registry references missing engineering JSON: {name}")

    authority_count = sum(
        1 for category in classified.values() if category == "authority"
    )
    if authority_count > MAX_AUTHORITY_JSON:
        errors.append(
            f"authority JSON budget exceeded: {authority_count}>{MAX_AUTHORITY_JSON}"
        )

    expected_prefix = {
        "authority": "",
        "schema": "schemas/",
        "example": "examples/",
        "fixture": "fixtures/",
        "authoritative_state": "state/",
    }
    for name, category in classified.items():
        prefix = expected_prefix[category]
        if prefix:
            if not name.startswith(prefix):
                errors.append(
                    f"{category} JSON must live under engineering/{prefix}: {name}"
                )
        elif "/" in name:
            errors.append(
                f"authority JSON must live at engineering root: {name}"
            )

    root_json = {path.name for path in ENGINEERING.glob("*.json")}
    authority_json = {
        name for name, category in classified.items() if category == "authority"
    }
    if root_json != authority_json:
        errors.append(
            "engineering root JSON surface must equal registered authority set"
        )

    for root in (ENGINEERING, DOCS):
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for legacy in LEGACY_EXECUTABLES:
                if legacy in text:
                    errors.append(
                        f"legacy executable reference remains: "
                        f"{path.relative_to(ROOT)} -> {legacy}"
                    )

    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"CONTROL SURFACE: FAIL: {error}")
        return 1
    registry = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    counts = {
        category: len(registry["json"][category])
        for category in JSON_CATEGORIES
    }
    print(
        "CONTROL SURFACE: PASS "
        f"(1 installed CLI; authority={counts['authority']}, "
        f"schema={counts['schema']}, example={counts['example']}, "
        f"fixture={counts['fixture']}, authoritative_state={counts['authoritative_state']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
