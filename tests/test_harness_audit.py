from pathlib import Path

from project_orchestrator.harness_audit import audit_harness, discover_surfaces


def policy(*, repositories=None, always_on=1024, task=4096, single=2048):
    return {
        "schema_version": 1,
        "mode": "READ_ONLY",
        "context_budget": {
            "always_on_max_bytes": always_on,
            "task_triggered_max_bytes": task,
            "single_surface_max_bytes": single,
            "approx_chars_per_token": 4,
            "budget_exceeded_severity": "WARNING",
        },
        "safety": {
            "auto_delete": False,
            "auto_rewrite": False,
            "product_reality_may_be_inferred": False,
            "missing_remote_repo_policy": "UNVERIFIED",
            "context_metrics_are_merge_authority": False,
            "repository_policy_remains_authoritative": True,
            "secret_like_literal_severity": "ERROR",
        },
        "surface_patterns": [
            {
                "glob": "AGENTS.md",
                "kind": "INSTRUCTION",
                "activation": "ALWAYS_ON",
                "counts_toward_context": True,
            },
            {
                "glob": "CLAUDE.md",
                "kind": "INSTRUCTION",
                "activation": "ALWAYS_ON",
                "counts_toward_context": True,
            },
            {
                "glob": ".claude/skills/**/*",
                "kind": "SKILL",
                "activation": "TASK_TRIGGERED",
                "counts_toward_context": True,
            },
            {
                "glob": ".mcp.json",
                "kind": "MCP_DESCRIPTOR",
                "activation": "TOOL_DESCRIPTOR",
                "counts_toward_context": True,
            },
        ],
        "secret_key_markers": ["token", "password", "secret", "api_key"],
        "safe_secret_value_markers": ["${", "$env", "redacted", "example", "your_"],
        "repositories": repositories
        or [
            {
                "name": "example/repo",
                "required_paths": ["AGENTS.md"],
                "authoritative_paths": ["AGENTS.md"],
                "advisory_paths": ["CLAUDE.md"],
            }
        ],
    }


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def codes(result):
    return {item["code"] for item in result["findings"]}


def test_green_read_only_audit_for_mounted_repo(tmp_path):
    write(tmp_path / "AGENTS.md", "repository rules\n")
    result = audit_harness(policy(), {"example/repo": tmp_path})
    assert result["status"] == "GREEN"
    assert result["read_only"] is True
    assert result["product_reality_inferred"] is False
    assert result["context_metrics_are_merge_authority"] is False
    assert result["summary"]["repositories_audited"] == 1
    assert result["summary"]["estimated_tokens"] > 0


def test_duplicate_always_on_context_is_visible_not_silently_counted(tmp_path):
    write(tmp_path / "AGENTS.md", "same rules\n")
    write(tmp_path / "CLAUDE.md", "same rules\n")
    result = audit_harness(policy(), {"example/repo": tmp_path})
    assert result["status"] == "YELLOW"
    assert "DUPLICATE_CONTEXT_SURFACE" in codes(result)


def test_context_budget_overage_is_advisory_warning(tmp_path):
    write(tmp_path / "AGENTS.md", "x" * 100)
    result = audit_harness(
        policy(always_on=16, single=32),
        {"example/repo": tmp_path},
    )
    assert result["status"] == "YELLOW"
    assert "ALWAYS_ON_CONTEXT_BUDGET_EXCEEDED" in codes(result)
    assert "CONTEXT_SURFACE_TOO_LARGE" in codes(result)
    assert result["summary"]["error_count"] == 0


def test_missing_required_surface_fails_closed(tmp_path):
    result = audit_harness(policy(), {"example/repo": tmp_path})
    assert result["status"] == "RED"
    assert "MISSING_REQUIRED_SURFACE" in codes(result)


def test_authority_overlap_is_red_even_before_execution(tmp_path):
    write(tmp_path / "AGENTS.md", "rules\n")
    value = policy()
    value["repositories"][0]["advisory_paths"] = ["AGENTS.md"]
    result = audit_harness(value, {"example/repo": tmp_path})
    assert result["status"] == "RED"
    assert "AUTHORITY_OVERLAP" in codes(result)


def test_literal_secret_like_mcp_value_is_red(tmp_path):
    write(tmp_path / "AGENTS.md", "rules\n")
    write(tmp_path / ".mcp.json", '{"servers":{"github":{"token":"literal-credential"}}}')
    result = audit_harness(policy(), {"example/repo": tmp_path})
    assert result["status"] == "RED"
    assert "SECRET_LIKE_LITERAL" in codes(result)


def test_environment_reference_in_mcp_is_not_flagged_as_literal_secret(tmp_path):
    write(tmp_path / "AGENTS.md", "rules\n")
    write(tmp_path / ".mcp.json", '{"servers":{"github":{"token":"${GITHUB_TOKEN}"}}}')
    result = audit_harness(policy(), {"example/repo": tmp_path})
    assert result["status"] == "GREEN"
    assert "SECRET_LIKE_LITERAL" not in codes(result)


def test_unmounted_repository_is_unverified_not_fabricated(tmp_path):
    write(tmp_path / "AGENTS.md", "rules\n")
    repositories = [
        {
            "name": "example/repo",
            "required_paths": ["AGENTS.md"],
            "authoritative_paths": ["AGENTS.md"],
            "advisory_paths": [],
        },
        {
            "name": "example/remote",
            "required_paths": ["AGENTS.md"],
            "authoritative_paths": ["AGENTS.md"],
            "advisory_paths": [],
        },
    ]
    result = audit_harness(policy(repositories=repositories), {"example/repo": tmp_path})
    assert result["status"] == "YELLOW"
    assert result["summary"]["repositories_unverified"] == 1
    assert "REPOSITORY_UNVERIFIED" in codes(result)


def test_specific_surface_pattern_wins_before_general_pattern(tmp_path):
    write(tmp_path / "AGENTS.md", "rules\n")
    write(tmp_path / ".claude/skills/test/SKILL.md", "skill\n")
    surfaces = discover_surfaces(
        tmp_path,
        policy()["surface_patterns"],
        chars_per_token=4,
    )
    skill = next(item for item in surfaces if item["path"].endswith("SKILL.md"))
    assert skill["kind"] == "SKILL"
    assert skill["activation"] == "TASK_TRIGGERED"
