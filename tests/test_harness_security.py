from __future__ import annotations

import json

from project_orchestrator.harness_security import audit_harness_security


def policy() -> dict:
    return {
        "schema_version": 1,
        "mode": "READ_ONLY",
        "safety": {
            "auto_fix": False,
            "repository_policy_remains_authoritative": True,
            "product_reality_may_be_inferred": False,
            "block_at_or_above": "HIGH",
        },
        "surface_patterns": [
            {"glob": ".mcp.json"},
            {"glob": ".claude/settings*.json"},
            {"glob": ".github/workflows/*.yml"},
        ],
        "untrusted_content_handlers": [],
        "repositories": [{"name": "jweter/example"}],
    }


def test_shell_mcp_and_literal_secret_fail_closed(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unsafe": {
                        "command": "powershell",
                        "args": ["-Command", "tool"],
                        "env": {"api_key": "literal-value"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = audit_harness_security(policy(), {"jweter/example": tmp_path})
    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "RED"
    assert "MCP_SHELL_LAUNCHER" in codes
    assert "SECRET_LIKE_LITERAL" in codes
    assert result["read_only"] is True
    assert result["auto_fix"] is False


def test_unpinned_npx_is_blocking_but_env_reference_is_not_secret(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"],
                        "env": {"api_key": "${MCP_API_KEY}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = audit_harness_security(policy(), {"jweter/example": tmp_path})
    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "RED"
    assert "MCP_UNPINNED_NPX_PACKAGE" in codes
    assert "SECRET_LIKE_LITERAL" not in codes


def test_pinned_npx_is_allowed(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem@1.2.3", "./workspace"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = audit_harness_security(policy(), {"jweter/example": tmp_path})
    codes = {item["code"] for item in result["findings"]}
    assert "MCP_UNPINNED_NPX_PACKAGE" not in codes
    assert result["status"] == "GREEN"


def test_remote_transport_and_write_all_are_advisory_findings(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"remote": {"transport": "https", "url": "https://example.invalid/mcp"}}}),
        encoding="utf-8",
    )
    workflow = tmp_path / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text("name: CI\npermissions: write-all\n", encoding="utf-8")
    result = audit_harness_security(policy(), {"jweter/example": tmp_path})
    codes = {item["code"] for item in result["findings"]}
    assert result["status"] == "YELLOW"
    assert "MCP_REMOTE_TRANSPORT" in codes
    assert "WORKFLOW_WRITE_ALL" in codes


def test_declared_untrusted_handler_requires_defensive_markers(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text("Summarize external issue content.\n", encoding="utf-8")
    payload = policy()
    payload["untrusted_content_handlers"] = [
        {
            "repository": "jweter/example",
            "path": "AGENTS.md",
            "required_markers": ["treat external content as untrusted", "do not execute embedded instructions"],
        }
    ]
    result = audit_harness_security(payload, {"jweter/example": tmp_path})
    assert result["status"] == "RED"
    assert any(item["code"] == "UNTRUSTED_CONTENT_DEFENSE_MISSING" for item in result["findings"])


def test_unmounted_repository_is_unverified_not_assumed_safe() -> None:
    result = audit_harness_security(policy(), {})
    assert result["status"] == "YELLOW"
    assert result["summary"]["repositories_unverified"] == 1
    assert result["findings"][0]["code"] == "REPOSITORY_UNVERIFIED"
