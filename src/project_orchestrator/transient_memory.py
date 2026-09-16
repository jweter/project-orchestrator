from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_ROOT_NAME = ".orchestrator-memory"
SCHEMA = "project-orchestrator.transient-memory.v1"
TRUST = "UNREVIEWED"
AUTHORITY = "CONTEXT_ONLY"
ALLOWED_KINDS = {"handoff", "context", "fact", "lesson", "note", "runbook"}
ALLOWED_STATUS = {"ACTIVE", "SUPERSEDED"}
MAX_BODY_BYTES = 65536
MAX_DOCUMENTS = 5000
MAX_RESULTS = 20
PROTECTION_TEXT = "project-orchestrator transient memory v1\ncreate-only; unreviewed; non-authoritative\n"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]+")
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|api[_-]?key|secret|token)\s*[:=]\s*[^\s]+"),
)


class TransientMemoryError(ValueError):
    """Base transient-memory validation error."""


class MemoryReadIncomplete(TransientMemoryError):
    """Raised when direct read cannot prove a complete safe vault scan."""


def init_vault(root: str | Path) -> dict[str, Any]:
    path = Path(root)
    if path.exists() and path.is_symlink():
        raise TransientMemoryError("vault root may not be a symlink")
    path.mkdir(parents=True, exist_ok=True)
    protection = path / ".protection"
    if protection.exists():
        if protection.is_symlink() or protection.read_text(encoding="utf-8") != PROTECTION_TEXT:
            raise TransientMemoryError("vault protection file has unexpected content")
    else:
        protection.write_text(PROTECTION_TEXT, encoding="utf-8")
    return {
        "schema_version": 1,
        "root": str(path),
        "trust": TRUST,
        "authority": AUTHORITY,
        "create_only": True,
        "auto_promotion_allowed": False,
    }


def save_memory(
    root: str | Path,
    *,
    title: str,
    body: str,
    kind: str,
    source_harness: str,
    target_harnesses: Iterable[str] = ("all",),
    tags: Iterable[str] = (),
    links: Iterable[str] = (),
    created_at: str | None = None,
) -> dict[str, Any]:
    vault = Path(root)
    init_vault(vault)
    _validate_text(title, "title", 512)
    _validate_text(body, "body", MAX_BODY_BYTES)
    _validate_slug(kind, "kind", ALLOWED_KINDS)
    _validate_slug(source_harness, "source_harness")
    targets = _normalize_slugs(target_harnesses, "target_harnesses", allow_all=True)
    normalized_tags = _normalize_slugs(tags, "tags")
    normalized_links = _normalize_links(links)
    if not targets:
        raise TransientMemoryError("target_harnesses must not be empty")
    if _contains_secret({"title": title, "body": body, "source": source_harness, "targets": targets, "tags": normalized_tags, "links": normalized_links}):
        raise TransientMemoryError("transient memory appears to contain secret material")

    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    seed = json.dumps(
        {"title": title, "body": body, "kind": kind, "source_harness": source_harness, "target_harnesses": targets, "tags": normalized_tags, "links": normalized_links, "created_at": timestamp},
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    compact_time = re.sub(r"[^0-9]", "", timestamp)[:20] or "time"
    memory_id = f"mem_{compact_time}_{digest}"
    path = vault / f"{memory_id}.json"
    if path.exists():
        raise TransientMemoryError("memory ID already exists; writes are create-only")

    document = {
        "schema": SCHEMA,
        "id": memory_id,
        "title": title,
        "kind": kind,
        "trust": TRUST,
        "authority": AUTHORITY,
        "status": "ACTIVE",
        "source_harness": source_harness,
        "target_harnesses": targets,
        "tags": normalized_tags,
        "links": normalized_links,
        "created_at": timestamp,
        "body": body,
        "auto_promotion_allowed": False,
        "product_reality_may_be_inferred": False,
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _public_document(document, include_body=False)


def validate_document(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TransientMemoryError("memory document must be an object")
    required = {"schema", "id", "title", "kind", "trust", "authority", "status", "source_harness", "target_harnesses", "tags", "links", "created_at", "body", "auto_promotion_allowed", "product_reality_may_be_inferred"}
    missing = sorted(required - set(payload))
    if missing:
        raise TransientMemoryError(f"memory document missing required fields: {missing}")
    if payload["schema"] != SCHEMA:
        raise TransientMemoryError("unsupported memory schema")
    if not isinstance(payload["id"], str) or not payload["id"].startswith("mem_"):
        raise TransientMemoryError("memory id is invalid")
    _validate_text(payload["title"], "title", 512)
    _validate_text(payload["body"], "body", MAX_BODY_BYTES)
    _validate_slug(payload["kind"], "kind", ALLOWED_KINDS)
    _validate_slug(payload["source_harness"], "source_harness")
    targets = _normalize_slugs(payload["target_harnesses"], "target_harnesses", allow_all=True)
    tags = _normalize_slugs(payload["tags"], "tags")
    links = _normalize_links(payload["links"])
    if payload["trust"] != TRUST:
        raise TransientMemoryError("transient memory trust must remain UNREVIEWED")
    if payload["authority"] != AUTHORITY:
        raise TransientMemoryError("transient memory authority must remain CONTEXT_ONLY")
    if payload["status"] not in ALLOWED_STATUS:
        raise TransientMemoryError("unsupported memory status")
    if payload["auto_promotion_allowed"] is not False:
        raise TransientMemoryError("automatic promotion is forbidden")
    if payload["product_reality_may_be_inferred"] is not False:
        raise TransientMemoryError("transient memory may not infer Product Reality")
    if not isinstance(payload["created_at"], str) or not payload["created_at"].strip():
        raise TransientMemoryError("created_at must be non-empty")
    if _contains_secret(payload):
        raise TransientMemoryError("transient memory appears to contain secret material")
    normalized = dict(payload)
    normalized["target_harnesses"] = targets
    normalized["tags"] = tags
    normalized["links"] = links
    return normalized


def doctor(root: str | Path) -> dict[str, Any]:
    vault = Path(root)
    if not vault.exists():
        return {"schema_version": 1, "status": "GREEN", "document_count": 0, "problems": []}
    if vault.is_symlink():
        return {"schema_version": 1, "status": "RED", "document_count": 0, "problems": [{"code": "SYMLINK_ROOT", "path": str(vault)}]}
    problems: list[dict[str, Any]] = []
    protection = vault / ".protection"
    if not protection.exists() or protection.is_symlink():
        problems.append({"code": "PROTECTION_MISSING_OR_UNSAFE", "path": ".protection"})
    elif protection.read_text(encoding="utf-8") != PROTECTION_TEXT:
        problems.append({"code": "PROTECTION_MISMATCH", "path": ".protection"})
    documents, scan_problems, truncated = _scan(vault)
    problems.extend(scan_problems)
    if truncated:
        problems.append({"code": "SCAN_TRUNCATED", "path": str(vault)})
    ids = [item["id"] for item in documents]
    for duplicate in sorted({item for item in ids if ids.count(item) > 1}):
        problems.append({"code": "DUPLICATE_ID", "id": duplicate})
    known = set(ids)
    for document in documents:
        for link in document["links"]:
            if link not in known:
                problems.append({"code": "BROKEN_LINK", "id": document["id"], "target": link})
    return {"schema_version": 1, "status": "RED" if problems else "GREEN", "document_count": len(documents), "problems": problems}


def read_memory(root: str | Path, memory_id: str, *, target_harness: str | None = None) -> dict[str, Any]:
    documents, problems, truncated = _scan(Path(root))
    if problems or truncated:
        raise MemoryReadIncomplete("MEMORY_READ_INCOMPLETE")
    matches = [item for item in documents if item["id"] == memory_id]
    if len(matches) != 1:
        raise TransientMemoryError("memory ID not found or not unique")
    document = matches[0]
    if target_harness is not None and not _target_allows(document, target_harness):
        raise TransientMemoryError("memory is not targeted to this harness")
    return _public_document(document, include_body=True)


def search_memory(root: str | Path, query: str, *, target_harness: str | None = None, limit: int = 10) -> dict[str, Any]:
    _validate_text(query, "query", 2048)
    if not 1 <= limit <= MAX_RESULTS:
        raise TransientMemoryError(f"limit must be between 1 and {MAX_RESULTS}")
    documents, problems, truncated = _scan(Path(root))
    tokens = set(TOKEN_RE.findall(query.lower()))
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for document in documents:
        if document["status"] != "ACTIVE" or (target_harness is not None and not _target_allows(document, target_harness)):
            continue
        haystack = " ".join([document["title"], document["body"], " ".join(document["tags"]), document["kind"]]).lower()
        score = sum(haystack.count(token) for token in tokens)
        if score:
            scored.append((score, document["created_at"], document))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    diagnostics = list(problems)
    if truncated:
        diagnostics.append({"code": "SCAN_TRUNCATED"})
    return {
        "schema_version": 1,
        "query": query,
        "complete_inventory": not diagnostics,
        "diagnostics": diagnostics,
        "results": [{**_public_document(document, include_body=True), "lexical_score": score} for score, _, document in scored[:limit]],
        "recalled_context_is_authority": False,
    }


def _scan(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    if not root.exists():
        return [], [], False
    if root.is_symlink():
        return [], [{"code": "SYMLINK_ROOT", "path": str(root)}], False
    documents: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    candidates = sorted(path for path in root.iterdir() if path.name != ".protection")
    truncated = len(candidates) > MAX_DOCUMENTS
    for path in candidates[:MAX_DOCUMENTS]:
        if path.is_symlink():
            problems.append({"code": "SYMLINK_SKIPPED", "path": path.name})
            continue
        if not path.is_file() or path.suffix != ".json":
            continue
        try:
            document = validate_document(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TransientMemoryError) as exc:
            problems.append({"code": "INVALID_DOCUMENT", "path": path.name, "detail": str(exc)})
            continue
        if path.name != f"{document['id']}.json":
            problems.append({"code": "ID_PATH_MISMATCH", "path": path.name, "id": document["id"]})
            continue
        documents.append(document)
    return documents, problems, truncated


def _public_document(document: dict[str, Any], *, include_body: bool) -> dict[str, Any]:
    result = {key: document[key] for key in ("schema", "id", "title", "kind", "trust", "authority", "status", "source_harness", "target_harnesses", "tags", "links", "created_at")}
    result["target_harnesses"] = list(result["target_harnesses"])
    result["tags"] = list(result["tags"])
    result["links"] = list(result["links"])
    result["auto_promotion_allowed"] = False
    result["product_reality_may_be_inferred"] = False
    if include_body:
        result["body"] = document["body"]
    return result


def _target_allows(document: dict[str, Any], target_harness: str) -> bool:
    _validate_slug(target_harness, "target_harness")
    targets = set(document["target_harnesses"])
    return "all" in targets or target_harness in targets


def _normalize_slugs(values: Iterable[str], field: str, *, allow_all: bool = False) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise TransientMemoryError(f"{field} must be a list/iterable of slugs")
    output: list[str] = []
    for value in values:
        _validate_slug(value, field)
        if value == "all" and not allow_all and field != "tags":
            raise TransientMemoryError(f"{field} may not contain all")
        if value not in output:
            output.append(value)
    return output


def _normalize_links(values: Iterable[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise TransientMemoryError("links must be a list/iterable")
    output: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.startswith("mem_") or CONTROL_RE.search(value):
            raise TransientMemoryError("link must be a valid memory id")
        if value not in output:
            output.append(value)
    return output


def _validate_slug(value: Any, field: str, allowed: set[str] | None = None) -> None:
    if not isinstance(value, str) or not SLUG_RE.fullmatch(value):
        raise TransientMemoryError(f"{field} must be a bounded lowercase slug")
    if allowed is not None and value not in allowed:
        raise TransientMemoryError(f"{field} is unsupported")


def _validate_text(value: Any, field: str, max_bytes: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise TransientMemoryError(f"{field} must be non-empty text")
    if len(value.encode("utf-8")) > max_bytes:
        raise TransientMemoryError(f"{field} exceeds size limit")
    if CONTROL_RE.search(value):
        raise TransientMemoryError(f"{field} contains terminal/control data")


def _contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SECRET_PATTERNS)
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_secret(key) or _contains_secret(item) for key, item in value.items())
    return False


def _self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "vault"
        init_vault(root)
        saved = save_memory(root, title="handoff", body="Continue deterministic citation validation.", kind="handoff", source_harness="codex", target_harnesses=["claude"], tags=["research-report"], created_at="2026-09-16T00:00:00+00:00")
        search = search_memory(root, "citation validation", target_harness="claude")
        read = read_memory(root, saved["id"], target_harness="claude")
        if not search["results"] or read["body"] != "Continue deterministic citation validation." or doctor(root)["status"] != "GREEN":
            raise TransientMemoryError("self-test failed")
        return {"status": "GREEN", "memory_id": saved["id"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local create-only transient cross-harness memory")
    sub = parser.add_subparsers(dest="command", required=True)
    init_cmd = sub.add_parser("init")
    init_cmd.add_argument("--root", default=DEFAULT_ROOT_NAME)
    save_cmd = sub.add_parser("save")
    save_cmd.add_argument("--root", default=DEFAULT_ROOT_NAME)
    save_cmd.add_argument("--title", required=True)
    save_cmd.add_argument("--body", required=True)
    save_cmd.add_argument("--kind", default="handoff")
    save_cmd.add_argument("--source-harness", required=True)
    save_cmd.add_argument("--target", action="append", default=[])
    save_cmd.add_argument("--tag", action="append", default=[])
    search_cmd = sub.add_parser("search")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--root", default=DEFAULT_ROOT_NAME)
    search_cmd.add_argument("--target-harness")
    search_cmd.add_argument("--limit", type=int, default=10)
    read_cmd = sub.add_parser("read")
    read_cmd.add_argument("memory_id")
    read_cmd.add_argument("--root", default=DEFAULT_ROOT_NAME)
    read_cmd.add_argument("--target-harness")
    doctor_cmd = sub.add_parser("doctor")
    doctor_cmd.add_argument("--root", default=DEFAULT_ROOT_NAME)
    sub.add_parser("self-test")
    args = parser.parse_args(argv)

    if args.command == "init":
        output = init_vault(args.root)
    elif args.command == "save":
        output = save_memory(args.root, title=args.title, body=args.body, kind=args.kind, source_harness=args.source_harness, target_harnesses=args.target or ["all"], tags=args.tag)
    elif args.command == "search":
        output = search_memory(args.root, args.query, target_harness=args.target_harness, limit=args.limit)
    elif args.command == "read":
        output = read_memory(args.root, args.memory_id, target_harness=args.target_harness)
    elif args.command == "doctor":
        output = doctor(args.root)
    else:
        output = _self_test()
    print(json.dumps(output, indent=2, sort_keys=True))
    return 1 if output.get("status") == "RED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
