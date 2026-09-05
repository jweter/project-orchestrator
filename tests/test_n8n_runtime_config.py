from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
N8N_DIR = ROOT / "n8n"


def test_compose_forwards_public_webhook_url() -> None:
    compose = (N8N_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'N8N_WEBHOOK_URL: "${N8N_WEBHOOK_URL:-}"' in compose
    assert 'N8N_PROXY_HOPS: "${N8N_PROXY_HOPS:-1}"' in compose


def test_compose_does_not_use_legacy_webhook_url_var() -> None:
    compose = (N8N_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert "WEBHOOK_URL: \"${WEBHOOK_URL" not in compose


def test_compose_extends_ca_trust_without_disabling_verification() -> None:
    compose = (N8N_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert "NODE_EXTRA_CA_CERTS" in compose
    assert "NODE_TLS_REJECT_UNAUTHORIZED" not in compose


def test_env_template_declares_public_webhook_settings() -> None:
    env_example = (N8N_DIR / ".env.example").read_text(encoding="utf-8")
    assert "N8N_WEBHOOK_URL=" in env_example
    assert "N8N_PROXY_HOPS=1" in env_example


def test_env_template_does_not_use_legacy_webhook_url_var() -> None:
    env_example = (N8N_DIR / ".env.example").read_text(encoding="utf-8")
    assert "\nWEBHOOK_URL=" not in env_example
