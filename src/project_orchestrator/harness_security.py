from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "engineering" / "harness-security.json"
DEFAULT_REPOSITORY = "jweter/project-orchestrator"

SEVERITY_ORDER = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
KNOWN_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
SECRET_KEY_MARKERS = ("token", "password", "secret", "api_key", "apikey", "access_key", "private_key")
SAFE_VALUE_MARKERS = ("${", "$env", "env:", "redacted", "example", "changeme", "change_me", "your_", "<")
SHELL_COMMANDS = {"bash", "sh", "zsh", "fish", "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe"}
REMOTE_TRANSPORTS = {"http", "https", "sse", "streamable-http", "websocket", "ws", "wss"}
WRITE_PERMISSION_KEYS = {"actions", "checks", "contents", "deployments", "id-token", "issues", "packages", "pages", "pull-requests", "repository-projects", "security-events", "statuses"}


class HarnessSecurityError(ValueError):
    """Raised when the deterministic harness-security contract is malformed."""


def load_policy(path: str | Path = DEFAULT_POLICY) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HarnessSecurityError("harness-security policy must be a JSON object")
    return payload


def audit_harness_security(policy: dict[str, Any], repo_roots: dict[str, str | Path] | None = None) -> dict[str, Any]:
    """Read-only audit of agent, hook, MCP, permission, and credential surfaces."""
    _validate_policy(policy)
    roots = {name: Path(path) for name, path in (repo_roots or {}).items()}
    findings: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []

    def add(code: str, severity: str, message: str, *, repository: str, path: str | None = None, runtime_confidence: str = "HIGH") -> None:
        finding: dict[str, Any] = {
            "code": code,
            "severity": severity,
            "runtime_confidence": runtime_confidence,
            "repository": repository,
            "message": message,
        }
        if path is not None:
            finding["path"] = path
        findings.append(finding)

    for raw_repo in policy["repositories"]:
        repository = str(_mapping(raw_repo)["name"])
        root = roots.get(repository)
        if root is None:
            add("REPOSITORY_UNVERIFIED", "INFO", "repository root was not supplied; local harness security remains UNVERIFIED", repository=repository)
            reports.append({"repository": repository, "state": "UNVERIFIED", "surface_count": 0})
            continue
        if not root.exists() or not root.is_dir():
            add("REPOSITORY_ROOT_UNAVAILABLE", "HIGH", f"supplied repository root is unavailable: {root}", repository=repository)
            reports.append({"repository": repository, "state": "UNAVAILABLE", "surface_count": 0})
            continue

        surfaces = _discover_surfaces(root, policy["surface_patterns"])
        reports.append({"repository": repository, "state": "AUDITED", "surface_count": len(surfaces)})
        for surface in surfaces:
            relative = surface.relative_to(root).as_posix()
            try:
                text = surface.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                add("SURFACE_READ_FAILURE", "HIGH", str(exc), repository=repository, path=relative)
                continue
            if any(pattern.search(text) for pattern in KNOWN_SECRET_PATTERNS):
                add("KNOWN_SECRET_SHAPE", "CRITICAL", "known credential/private-key shape appears in a harness surface", repository=repository, path=relative)

            lower = surface.name.lower()
            if lower.endswith(".json"):
                _audit_json_surface(repository, relative, text, add)
            elif lower.endswith(".toml"):
                _audit_toml_surface(repository, relative, text, add)
            if relative.startswith(".github/workflows/") and lower.endswith((".yml", ".yaml")):
                _audit_workflow_permissions(repository, relative, text, add)

        _audit_declared_untrusted_handlers(policy, repository, root, add)

    block_at = policy["safety"]["block_at_or_above"]
    blocking = [f for f in findings if f["code"] != "REPOSITORY_UNVERIFIED" and SEVERITY_ORDER[f["severity"]] >= SEVERITY_ORDER[block_at]]
    status = "RED" if blocking else "YELLOW" if findings else "GREEN"
    return {
        "schema_version": 1,
        "mode": "READ_ONLY",
        "status": status,
        "read_only": True,
        "auto_fix": False,
        "repository_policy_remains_authoritative": True,
        "product_reality_inferred": False,
        "summary": {
            "repositories_declared": len(reports),
            "repositories_audited": sum(r["state"] == "AUDITED" for r in reports),
            "repositories_unverified": sum(r["state"] == "UNVERIFIED" for r in reports),
            "finding_count": len(findings),
            "blocking_finding_count": len(blocking),
            **{severity.lower(): sum(f["severity"] == severity for f in findings) for severity in SEVERITY_ORDER},
        },
        "repositories": reports,
        "findings": findings,
    }


def _discover_surfaces(root: Path, patterns: Iterable[Any]) -> list[Path]:
    found: dict[str, Path] = {}
    for raw in patterns:
        glob = _mapping(raw).get("glob")
        if not isinstance(glob, str) or not glob:
            continue
        for path in root.glob(glob):
            if path.is_file() and not path.is_symlink():
                found[path.relative_to(root).as_posix()] = path
    return [found[key] for key in sorted(found)]


def _audit_json_surface(repository: str, path: str, text: str, add: Any) -> None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        if "mcp" in path.lower() or "settings" in path.lower():
            add("HARNESS_JSON_INVALID", "HIGH", "harness JSON could not be parsed", repository=repository, path=path)
        return
    _scan_literal_secrets(payload, repository, path, add)
    if isinstance(payload, dict):
        servers = payload.get("mcpServers")
        if not isinstance(servers, dict):
            servers = payload.get("servers")
        if isinstance(servers, dict):
            for name, server in servers.items():
                if isinstance(server, dict):
                    _audit_mcp_server(repository, path, str(name), server, add)
    _scan_hook_commands(payload, repository, path, add)


def _audit_toml_surface(repository: str, path: str, text: str, add: Any) -> None:
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        add("HARNESS_TOML_INVALID", "HIGH", "harness TOML could not be parsed", repository=repository, path=path)
        return
    _scan_literal_secrets(payload, repository, path, add)
    _scan_hook_commands(payload, repository, path, add)


def _scan_literal_secrets(value: Any, repository: str, path: str, add: Any, prefix: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            key_path = f"{prefix}.{key_text}" if prefix else key_text
            if isinstance(item, str) and any(marker in key_text.lower() for marker in SECRET_KEY_MARKERS):
                stripped = item.strip()
                if stripped and not any(marker in stripped.lower() for marker in SAFE_VALUE_MARKERS):
                    add("SECRET_LIKE_LITERAL", "CRITICAL", f"literal value appears under secret-like key {key_path}", repository=repository, path=path)
            _scan_literal_secrets(item, repository, path, add, key_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_literal_secrets(item, repository, path, add, f"{prefix}[{index}]")


def _audit_mcp_server(repository: str, path: str, name: str, server: dict[str, Any], add: Any) -> None:
    command = server.get("command")
    command_name = Path(command).name.lower() if isinstance(command, str) and command else ""
    if command_name in SHELL_COMMANDS:
        add("MCP_SHELL_LAUNCHER", "HIGH", f"MCP server {name!r} launches through a general-purpose shell", repository=repository, path=path)

    args = server.get("args")
    arg_list = [str(item) for item in args] if isinstance(args, list) else []
    if command_name in {"npx", "npx.cmd", "npx.exe"}:
        package = next((arg for arg in arg_list if arg and not arg.startswith("-")), None)
        if package is None or not _is_pinned_npx_package(package):
            add("MCP_UNPINNED_NPX_PACKAGE", "HIGH", f"MCP server {name!r} does not pin its npx package", repository=repository, path=path)

    transport = str(server.get("transport", "")).lower()
    url = server.get("url")
    if transport in REMOTE_TRANSPORTS or (isinstance(url, str) and url.lower().startswith(("http://", "https://", "ws://", "wss://"))):
        add("MCP_REMOTE_TRANSPORT", "MEDIUM", f"MCP server {name!r} uses remote transport; verify endpoint trust and least privilege", repository=repository, path=path, runtime_confidence="MEDIUM")

    if any(arg.strip().replace("\\", "/").lower() in {"/", "c:/", "d:/", "e:/"} for arg in arg_list):
        add("MCP_BROAD_FILESYSTEM_SCOPE", "HIGH", f"MCP server {name!r} appears to receive a filesystem root", repository=repository, path=path)


def _is_pinned_npx_package(value: str) -> bool:
    if value.startswith("@"):
        slash = value.find("/")
        at = value.rfind("@")
        return slash >= 0 and at > slash and at < len(value) - 1 and value[at + 1 :].lower() not in {"latest", "*"}
    if "@" not in value:
        return False
    package, version = value.rsplit("@", 1)
    return bool(package and version and version.lower() not in {"latest", "*"})


def _scan_hook_commands(value: Any, repository: str, path: str, add: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"command", "cmd", "shell"} and isinstance(item, str):
                first = item.strip().split(maxsplit=1)[0].lower() if item.strip() else ""
                if Path(first).name.lower() in SHELL_COMMANDS:
                    add("HOOK_SHELL_COMMAND", "MEDIUM", "harness hook executes through a general-purpose shell; keep it versioned and bounded", repository=repository, path=path, runtime_confidence="MEDIUM")
            _scan_hook_commands(item, repository, path, add)
    elif isinstance(value, list):
        for item in value:
            _scan_hook_commands(item, repository, path, add)


def _audit_workflow_permissions(repository: str, path: str, text: str, add: Any) -> None:
    if re.search(r"(?mi)^\s*permissions\s*:\s*write-all\s*(?:#.*)?$", text):
        add("WORKFLOW_WRITE_ALL", "MEDIUM", "workflow requests write-all permissions; confirm least privilege is necessary", repository=repository, path=path)
        return
    writes = {m.group(1) for m in re.finditer(r"(?mi)^\s*([a-z-]+)\s*:\s*write\s*(?:#.*)?$", text) if m.group(1) in WRITE_PERMISSION_KEYS}
    if len(writes) >= 4:
        add("WORKFLOW_BROAD_WRITE_PERMISSIONS", "MEDIUM", f"workflow grants write permission to multiple scopes: {sorted(writes)}", repository=repository, path=path)


def _audit_declared_untrusted_handlers(policy: dict[str, Any], repository: str, root: Path, add: Any) -> None:
    handlers = policy.get("untrusted_content_handlers")
    if not isinstance(handlers, list):
        return
    for raw in handlers:
        item = _mapping(raw)
        if item.get("repository") != repository:
            continue
        path = item.get("path")
        markers = item.get("required_markers")
        if not isinstance(path, str) or not path or not isinstance(markers, list) or not markers:
            continue
        target = root / path
        if not target.exists() or not target.is_file() or target.is_symlink():
            add("UNTRUSTED_HANDLER_UNVERIFIED", "HIGH", "declared untrusted-content handler is unavailable for deterministic review", repository=repository, path=path)
            continue
        text = target.read_text(encoding="utf-8", errors="replace").lower()
        missing = [str(marker) for marker in markers if str(marker).lower() not in text]
        if missing:
            add("UNTRUSTED_CONTENT_DEFENSE_MISSING", "HIGH", f"declared untrusted-content handler lacks required defensive marker(s): {missing}", repository=repository, path=path)


def _validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema_version") != 1:
        raise HarnessSecurityError("schema_version must equal 1")
    if policy.get("mode") != "READ_ONLY":
        raise HarnessSecurityError("mode must equal READ_ONLY")
    safety = _mapping(policy.get("safety"))
    expected = {"auto_fix": False, "repository_policy_remains_authoritative": True, "product_reality_may_be_inferred": False}
    for key, value in expected.items():
        if safety.get(key) is not value:
            raise HarnessSecurityError(f"safety.{key} must equal {value!r}")
    if safety.get("block_at_or_above") not in SEVERITY_ORDER:
        raise HarnessSecurityError("safety.block_at_or_above is invalid")
    repositories = policy.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise HarnessSecurityError("repositories must be a non-empty list")
    names = [_mapping(item).get("name") for item in repositories]
    if any(not isinstance(name, str) or not name for name in names):
        raise HarnessSecurityError("repository name must be non-empty")
    if len(names) != len(set(names)):
        raise HarnessSecurityError("repository names must be unique")
    patterns = policy.get("surface_patterns")
    if not isinstance(patterns, list) or not patterns:
        raise HarnessSecurityError("surface_patterns must be a non-empty list")
    if any(not isinstance(_mapping(item).get("glob"), str) or not _mapping(item).get("glob") for item in patterns):
        raise HarnessSecurityError("each surface pattern requires a non-empty glob")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_repo_root(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("repo root must be REPOSITORY=PATH")
    repository, path = value.split("=", 1)
    if not repository or not path:
        raise argparse.ArgumentTypeError("repo root must be REPOSITORY=PATH")
    return repository, path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only harness security audit")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--repo-root", action="append", default=[], type=_parse_repo_root)
    args = parser.parse_args(argv)
    roots = dict(args.repo_root)
    if not roots:
        roots[DEFAULT_REPOSITORY] = str(ROOT)
    result = audit_harness_security(load_policy(args.policy), roots)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["status"] == "RED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
