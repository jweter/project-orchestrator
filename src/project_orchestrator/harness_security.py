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


def audit_harness_security(
    policy: dict[str, Any],
    repo_roots: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Read-only audit of agent/hook/MCP/security surfaces."""
    _validate_policy(policy)
    roots = {name: Path(path) for name, path in (repo_roots or {}).items()}
    findings: list[dict[str, Any]] = []
    repository_reports: list[dict[str, Any]] = []

    def add(
        code: str,
        severity: str,
        message: str,
        *,
        repository: str,
        path: str | None = None,
        runtime_confidence: str = "HIGH",
    ) -> None:
        item: dict[str, Any] = {
            "code": code,
            "severity": severity,
            "runtime_confidence": runtime_confidence,
            "repository": repository,
            "message": message,
        }
        if path is not None:
            item["path"] = path
        findings.append(item)

    patterns = policy["surface_patterns"]
    for repo_cfg_raw in policy["repositories"]:
        repo_cfg = _mapping(repo_cfg_raw)
        repository = str(repo_cfg["name"])
        root = roots.get(repository)
        if root is None:
            add(
                "REPOSITORY_UNVERIFIED",
                "INFO",
                "repository root was not supplied; local harness security remains UNVERIFIED",
                repository=repository,
            )
            repository_reports.append(
                {"repository": repository, "state": "UNVERIFIED", "surface_count": 0}
            )
            continue
        if not root.exists() or not root.is_dir():
            add(
                "REPOSITORY_ROOT_UNAVAILABLE",
                "HIGH",
                f"supplied repository root is unavailable: {root}",
                repository=repository,
            )
            repository_reports.append(
                {"repository": repository, "state": "UNAVAILABLE", "surface_count": 0}
            )
            continue

        surfaces = _discover_surfaces(root, patterns)
        repository_reports.append(
            {"repository": repository, "state": "AUDITED", "surface_count": len(surfaces)}
        )
        for surface in surfaces:
            relative = surface.relative_to(root).as_posix()
            try:
                raw = surface.read_bytes()
            except OSError as exc:
                add("SURFACE_READ_FAILURE", "HIGH", str(exc), repository=repository, path=relative)
                continue
            text = raw.decode("utf-8", errors="replace")
            for pattern in KNOWN_SECRET_PATTERNS:
                if pattern.search(text):
                    add(
                        "KNOWN_SECRET_SHAPE",
                        "CRITICAL",
                        "known credential/private-key shape appears in a harness surface",
                        repository=repository,
                        path=relative,
                    )
                    break

            lower_name = surface.name.lower()
            if lower_name.endswith(".json"):
                _audit_json_surface(repository, relative, text, add)
            elif lower_name.endswith(".toml"):
                _audit_toml_surface(repository, relative, text, add)

            if relative.startswith(".github/workflows/") and lower_name.endswith((".yml", ".yaml")):
                _audit_workflow_permissions(repository, relative, text, add)

        _audit_declared_untrusted_handlers(policy, repository, root, add)

    block_at = policy["safety"]["block_at_or_above"]
    blocking = [
        item
        for item in findings
        if SEVERITY_ORDER[item["severity"]] >= SEVERITY_ORDER[block_at]
        and item["code"] != "REPOSITORY_UNVERIFIED"
    ]
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
            "repositories_declared": len(repository_reports),
            "repositories_audited": sum(1 for item in repository_reports if item["state"] == "AUDITED"),
            "repositories_unverified": sum(1 for item in repository_reports if item["state"] == "UNVERIFIED"),
            "finding_count": len(findings),
            "blocking_finding_count": len(blocking),
            "critical": sum(1 for item in findings if item["severity"] == "CRITICAL"),
            "high": sum(1 for item in findings if item["severity"] == "HIGH"),
            "medium": sum(1 for item in findings if item["severity"] == "MEDIUM"),
            "low": sum(1 for item in findings if item["severity"] == "LOW"),
            "info": sum(1 for item in findings if item["severity"] == "INFO"),
        },
        "repositories": repository_reports,
        "findings": findings,
    }


def _discover_surfaces(root: Path, patterns: Iterable[Any]) -> list[Path]:
    seen: set[str] = set()
    output: list[Path] = []
    for raw in patterns:
        rule = _mapping(raw)
        glob = rule.get("glob")
        if not isinstance(glob, str) or not glob:
            continue
        for candidate in sorted(root.glob(glob)):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = candidate.relative_to(root).as_posix()
            if relative not in seen:
                seen.add(relative)
                output.append(candidate)
    return output


def _audit_json_surface(repository: str, path: str, text: str, add: Any) -> None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        if "mcp" in path.lower() or "settings" in path.lower():
            add("HARNESS_JSON_INVALID", "HIGH", "harness JSON could not be parsed", repository=repository, path=path)
        return

    _scan_literal_secrets(payload, repository, path, add)

    if not isinstance(payload, dict):
        return
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        servers = payload.get("servers")
    if isinstance(servers, dict):
        for server_name, raw_server in servers.items():
            if isinstance(raw_server, dict):
                _audit_mcp_server(repository, path, str(server_name), raw_server, add)

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
                    add(
                        "SECRET_LIKE_LITERAL",
                        "CRITICAL",
                        f"literal value appears under secret-like key {key_path}",
                        repository=repository,
                        path=path,
                    )
            _scan_literal_secrets(item, repository, path, add, key_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_literal_secrets(item, repository, path, add, f"{prefix}[{index}]")


def _audit_mcp_server(repository: str, path: str, name: str, server: dict[str, Any], add: Any) -> None:
    command = server.get("command")
    command_name = Path(command).name.lower() if isinstance(command, str) and command else ""
    if command_name in SHELL_COMMANDS:
        add(
            "MCP_SHELL_LAUNCHER",
            "HIGH",
            f"MCP server {name!r} launches through a general-purpose shell",
            repository=repository,
            path=path,
        )

    args = server.get("args")
    arg_list = [str(item) for item in args] if isinstance(args, list) else []
    if command_name in {"npx", "npx.cmd", "npx.exe"}:
        packages = [arg for arg in arg_list if arg and not arg.startswith("-")]
        for package in packages:
            if not _is_pinned_npx_package(package):
                add(
                    "MCP_UNPINNED_NPX_PACKAGE",
                    "HIGH",
                    f"MCP server {name!r} executes unpinned npx package {package!r}",
                    repository=repository,
                    path=path,
                )
                break

    transport = str(server.get("transport", "")).lower()
    url = server.get("url")
    if transport in REMOTE_TRANSPORTS or (
        isinstance(url, str) and url.lower().startswith(("http://", "https://", "ws://", "wss://"))
    ):
        add(
            "MCP_REMOTE_TRANSPORT",
            "MEDIUM",
            f"MCP server {name!r} uses remote transport; verify endpoint trust and least privilege",
            repository=repository,
            path=path,
            runtime_confidence="MEDIUM",
        )

    for arg in arg_list:
        normalized = arg.strip().replace("\\", "/")
        if normalized in {"/", "c:/", "d:/", "e:/"}:
            add(
                "MCP_BROAD_FILESYSTEM_SCOPE",
                "HIGH",
                f"MCP server {name!r} appears to receive a filesystem root",
                repository=repository,
                path=path,
            )
            break


def _is_pinned_npx_package(value: str) -> bool:
    if value.startswith("@"):
        slash = value.find("/")
        if slash < 0:
            return False
        at = value.rfind("@")
        return at > slash and at < len(value) - 1 and value[at + 1 :].lower() not in {"latest", "*"}
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
                    add(
                        "HOOK_SHELL_COMMAND",
                        "MEDIUM",
                        "harness hook executes through a general-purpose shell; keep it versioned and bounded",
                        repository=repository,
                        path=path,
                        runtime_confidence="MEDIUM",
                    )
            _scan_hook_commands(item, repository, path, add)
    elif isinstance(value, list):
        for item in value:
            _scan_hook_commands(item, repository, path, add)


def _audit_workflow_permissions(repository: str, path: str, text: str, add: Any) -> None:
    if re.search(r"(?mi)^\s*permissions\s*:\s*write-all\s*(?:#.*)?$", text):
        add(
            "WORKFLOW_WRITE_ALL",
            "MEDIUM",
            "workflow requests write-all permissions; confirm least privilege is necessary",
            repository=repository,
            path=path,
        )
        return
    writes = {
        match.group(1)
        for match in re.finditer(r"(?mi)^\s*([a-z-]+)\s*:\s*write\s*(?:#.*)?$", text)
        if match.group(1) in WRITE_PERMISSION_KEYS
    }
    if len(writes) >= 4:
        add(
            "WORKFLOW_BROAD_WRITE_PERMISSIONS",
            "MEDIUM",
            f"workflow grants write permission to multiple scopes: {sorted(writes)}",
            repository=repository,
            path=path,
        )


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
            add(
                "UNTRUSTED_HANDLER_UNVERIFIED",
                "HIGH",
                "declared untrusted-content handler is unavailable for deterministic review",
                repository=repository,
                path=path,
            )
            continue
        text = target.read_text(encoding="utf-8", errors="replace").lower()
        missing = [str(marker) for marker in markers if str(marker).lower() not in text]
        if missing:
            add(
                "UNTRUSTED_CONTENT_DEFENSE_MISSING",
                "HIGH",
                f"declared untrusted-content handler lacks required defensive marker(s): {missing}",
                repository=repository,
                path=path,
            )


def _validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema_version") != 1:
        raise HarnessSecurityError("schema_version must equal 1")
    if policy.get("mode") != "READ_ONLY":
        raise HarnessSecurityError("mode must equal READ_ONLY")
    safety = _mapping(policy.get("safety"))
    required = {
        "auto_fix": False,
        "repository_policy_remains_authoritative": True,
        "product_reality_may_be_inferred": False,
    }
    for key, expected in required.items():
        if safety.get(key) is not expected:
            raise HarnessSecurityError(f"safety.{key} must equal {expected!r}")
    block_at = safety.get("block_at_or_above")
    if block_at not in SEVERITY_ORDER:
        raise HarnessSecurityError("safety.block_at_or_above is invalid")

    repositories = policy.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise HarnessSecurityError("repositories must be a non-empty list")
    names = []
    for raw in repositories:
        item = _mapping(raw)
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise HarnessSecurityError("repository name must be non-empty")
        names.append(name)
    if len(names) != len(set(names)):
        raise HarnessSecurityError("repository names must be unique")

    patterns = policy.get("surface_patterns")
    if not isinstance(patterns, list) or not patterns:
        raise HarnessSecurityError("surface_patterns must be a non-empty list")
    for raw in patterns:
        item = _mapping(raw)
        if not isinstance(item.get("glob"), str) or not item["glob"]:
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
