from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .learning import _contains_secret, load_memory, validate_memory

SCHEMA_VERSION = 1
DEFAULT_MEMORY = Path("engineering/state/learning-memory.json")
DEFAULT_CATALOG = Path("engineering/repository-skills.json")
ALLOWED_STATUSES = frozenset({"PILOT_READY", "ACTIVE_ADVISORY"})
AUTHORITY = "ADVISORY_ONLY"
_HISTORY_REF = re.compile(r"^(?:commit:)?([0-9a-fA-F]{40})$")


class SkillExtractionError(ValueError):
    """Raised when a repository-specific skill violates the evidence contract."""


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(_nonempty(item) for item in value)


def _skill_identity(skill: dict[str, Any]) -> str:
    stable = {key: skill[key] for key in sorted(skill) if key != "skill_id"}
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def validate_skill(raw: Any, memory: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SkillExtractionError("skill must be an object")
    fields = {
        "skill_key",
        "project",
        "repository",
        "status",
        "authority",
        "source_lesson_ids",
        "history_refs",
        "path_patterns",
        "task_triggers",
        "problem",
        "prevention_rule",
        "verification_commands",
        "residual_risk",
        "effectiveness_state",
        "product_reality_may_be_inferred",
    }
    if set(raw) - {"skill_id"} != fields:
        raise SkillExtractionError(f"skill fields must be exactly {sorted(fields)} plus optional skill_id")
    normalized_memory = validate_memory(memory)
    lessons = {entry["lesson_id"]: entry for entry in normalized_memory["entries"]}

    for field in ("skill_key", "project", "repository", "problem", "prevention_rule", "residual_risk"):
        if not _nonempty(raw[field]):
            raise SkillExtractionError(f"{field} must be a non-empty string")
    if raw["status"] not in ALLOWED_STATUSES:
        raise SkillExtractionError(f"status must be one of {sorted(ALLOWED_STATUSES)}")
    if raw["authority"] != AUTHORITY:
        raise SkillExtractionError("repository skills are advisory only")
    if raw["effectiveness_state"] != "UNMEASURED":
        raise SkillExtractionError("new repository skills must begin effectiveness_state=UNMEASURED")
    if raw["product_reality_may_be_inferred"] is not False:
        raise SkillExtractionError("repository skills may not infer Product Reality")

    for field in ("source_lesson_ids", "history_refs", "path_patterns", "task_triggers", "verification_commands"):
        if not _nonempty_list(raw[field]):
            raise SkillExtractionError(f"{field} must be a non-empty list of strings")
    if len(set(raw["source_lesson_ids"])) != len(raw["source_lesson_ids"]):
        raise SkillExtractionError("source_lesson_ids must be unique")
    if len(set(raw["history_refs"])) != len(raw["history_refs"]):
        raise SkillExtractionError("history_refs must be unique")

    source_lessons: list[dict[str, Any]] = []
    for lesson_id in raw["source_lesson_ids"]:
        lesson = lessons.get(lesson_id)
        if lesson is None:
            raise SkillExtractionError(f"source lesson is not present in verified memory: {lesson_id}")
        if lesson["repository"] != raw["repository"]:
            raise SkillExtractionError("skill may not silently transfer a lesson across repositories")
        if lesson["project"] != raw["project"]:
            raise SkillExtractionError("skill project must match every source lesson")
        source_lessons.append(lesson)

    for ref in raw["history_refs"]:
        if _HISTORY_REF.fullmatch(ref) is None:
            raise SkillExtractionError("history_refs must be exact 40-character commit SHAs, optionally prefixed commit:")

    if any(pattern.startswith("/") or ".." in Path(pattern).parts for pattern in raw["path_patterns"]):
        raise SkillExtractionError("path_patterns must be repository-relative and may not contain '..'")
    if _contains_secret(raw):
        raise SkillExtractionError("skill appears to contain secret material")

    normalized = {key: raw[key] for key in fields}
    normalized["source_lesson_ids"] = sorted(set(raw["source_lesson_ids"]))
    normalized["history_refs"] = sorted(set(ref.lower() for ref in raw["history_refs"]))
    normalized["path_patterns"] = sorted(set(raw["path_patterns"]))
    normalized["task_triggers"] = sorted(set(trigger.lower().strip() for trigger in raw["task_triggers"]))
    normalized["verification_commands"] = list(raw["verification_commands"])
    normalized["skill_id"] = _skill_identity(normalized)
    supplied = raw.get("skill_id")
    if supplied is not None and supplied != normalized["skill_id"]:
        raise SkillExtractionError("skill_id does not match skill content")
    return normalized


def validate_catalog(raw: Any, memory: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "skills"}:
        raise SkillExtractionError("repository-skill catalog must contain schema_version and skills")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise SkillExtractionError("repository-skill catalog schema_version must be 1")
    if not isinstance(raw["skills"], list):
        raise SkillExtractionError("skills must be a list")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    keys: set[tuple[str, str]] = set()
    for item in raw["skills"]:
        skill = validate_skill(item, memory)
        key = (skill["repository"], skill["skill_key"])
        if skill["skill_id"] in ids or key in keys:
            raise SkillExtractionError("duplicate repository skill")
        ids.add(skill["skill_id"])
        keys.add(key)
        normalized.append(skill)
    normalized.sort(key=lambda item: (item["repository"], item["skill_key"]))
    return {"schema_version": SCHEMA_VERSION, "skills": normalized}


def load_catalog(path: str | Path, memory: dict[str, Any]) -> dict[str, Any]:
    return validate_catalog(json.loads(Path(path).read_text(encoding="utf-8")), memory)


def _path_hits(skill: dict[str, Any], changed_paths: list[str]) -> list[str]:
    hits: list[str] = []
    for path in changed_paths:
        normalized = path.replace("\\", "/")
        if any(fnmatch.fnmatch(normalized, pattern) for pattern in skill["path_patterns"]):
            hits.append(path)
    return sorted(set(hits))


def _trigger_hits(skill: dict[str, Any], task_summary: str) -> list[str]:
    lowered = task_summary.lower()
    return sorted(trigger for trigger in skill["task_triggers"] if trigger in lowered)


def select_skills(
    catalog: dict[str, Any],
    memory: dict[str, Any],
    *,
    repository: str,
    changed_paths: list[str],
    task_summary: str = "",
) -> dict[str, Any]:
    normalized = validate_catalog(catalog, memory)
    if not _nonempty(repository):
        raise SkillExtractionError("repository must be a non-empty string")
    if not isinstance(changed_paths, list) or any(not _nonempty(path) for path in changed_paths):
        raise SkillExtractionError("changed_paths must be a list of non-empty strings")
    selected: list[dict[str, Any]] = []
    for skill in normalized["skills"]:
        if skill["repository"] != repository:
            continue
        path_hits = _path_hits(skill, changed_paths)
        trigger_hits = _trigger_hits(skill, task_summary)
        if not path_hits and not trigger_hits:
            continue
        selected.append(
            {
                "skill_id": skill["skill_id"],
                "skill_key": skill["skill_key"],
                "status": skill["status"],
                "authority": AUTHORITY,
                "source_lesson_ids": skill["source_lesson_ids"],
                "history_refs": skill["history_refs"],
                "matched_paths": path_hits,
                "matched_task_triggers": trigger_hits,
                "problem": skill["problem"],
                "prevention_rule": skill["prevention_rule"],
                "verification_commands": skill["verification_commands"],
                "residual_risk": skill["residual_risk"],
                "effectiveness_state": skill["effectiveness_state"],
            }
        )
    selected.sort(key=lambda item: item["skill_key"])
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "changed_paths": sorted(set(changed_paths)),
        "task_summary": task_summary,
        "selected_skills": selected,
        "selection_state": "ADVISORY_SKILLS_SELECTED" if selected else "NO_EXPLICIT_SKILL_MATCH",
        "authority_note": "repository skills are verified-history-derived advisory context only; live repository authority and Product Reality gates remain controlling",
    }


def build_report(catalog: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    normalized = validate_catalog(catalog, memory)
    by_repo: dict[str, int] = {}
    for skill in normalized["skills"]:
        by_repo[skill["repository"]] = by_repo.get(skill["repository"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "skill_count": len(normalized["skills"]),
        "skills_by_repository": dict(sorted(by_repo.items())),
        "pilot_ready": sum(1 for skill in normalized["skills"] if skill["status"] == "PILOT_READY"),
        "active_advisory": sum(1 for skill in normalized["skills"] if skill["status"] == "ACTIVE_ADVISORY"),
        "effectiveness_measured": 0,
        "effectiveness_state": "UNMEASURED" if normalized["skills"] else "INSUFFICIENT_DATA",
        "product_reality_may_be_inferred": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="project-orchestrator skill")
    parser.add_argument("--memory", type=Path, default=DEFAULT_MEMORY)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("report")
    select = sub.add_parser("select")
    select.add_argument("--repository", required=True)
    select.add_argument("--path", action="append", default=[])
    select.add_argument("--task-summary", default="")
    args = parser.parse_args(argv)

    memory = load_memory(args.memory)
    catalog = load_catalog(args.catalog, memory)
    if args.command == "validate":
        payload = {"status": "PASS", **build_report(catalog, memory)}
    elif args.command == "report":
        payload = build_report(catalog, memory)
    else:
        payload = select_skills(
            catalog,
            memory,
            repository=args.repository,
            changed_paths=args.path,
            task_summary=args.task_summary,
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
