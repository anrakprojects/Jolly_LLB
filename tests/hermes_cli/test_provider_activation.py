"""POST /api/providers/{id}/activate — verify credentials, then make primary."""

import json
import os
from pathlib import Path

import httpx
import pytest
import yaml


@pytest.fixture
def client(monkeypatch):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import hermes_state
    from hermes_constants import get_hermes_home
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", get_hermes_home() / "state.db")
    monkeypatch.delenv("ANRAK_JOLLY_API_KEY", raising=False)
    c = TestClient(app)
    c.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return c


@pytest.fixture
def jolly_server(monkeypatch):
    """Fake Jolly upstream: accepts only the keys in ``valid``."""
    state = {"valid": {"good-key"}, "seen": []}

    def handle(self, request):
        auth = request.headers.get("authorization", "")
        state["seen"].append(auth)
        if auth.removeprefix("Bearer ") not in state["valid"]:
            return httpx.Response(401, json={"error": {"message": "invalid key"}})
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 1,
                "model": "anraklegal/jolly",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            },
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle)
    return state


def _config() -> dict:
    return yaml.safe_load((Path(os.environ["HERMES_HOME"]) / "config.yaml").read_text()) or {}


def _write_config(data: dict) -> None:
    (Path(os.environ["HERMES_HOME"]) / "config.yaml").write_text(yaml.safe_dump(data))


def test_activation_without_credentials_reports_no_credentials(client, jolly_server):
    resp = client.post("/api/providers/anrak/activate", json={"model": "anraklegal/jolly"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["reason"] == "no_credentials"
    assert jolly_server["seen"] == []


def test_valid_key_activates_and_keeps_previous_primary_as_fallback(client, jolly_server):
    _write_config({"model": {"provider": "openai-codex", "default": "gpt-5.5", "base_url": "https://x"}})

    resp = client.post(
        "/api/providers/anrak/activate", json={"model": "anraklegal/jolly", "api_key": "good-key"}
    )

    body = resp.json()
    assert body["ok"] is True, body
    cfg = _config()
    assert cfg["model"]["provider"] == "anrak"
    assert cfg["model"]["default"] == "anraklegal/jolly"
    assert not cfg["model"].get("base_url")
    assert cfg["fallback_providers"][0] == {"provider": "openai-codex", "model": "gpt-5.5"}
    from hermes_cli.config import get_env_value

    assert get_env_value("ANRAK_JOLLY_API_KEY") == "good-key"


def test_rejected_key_is_not_kept(client, jolly_server):
    _write_config({"model": {"provider": "openai-codex", "default": "gpt-5.5"}})

    resp = client.post(
        "/api/providers/anrak/activate", json={"model": "anraklegal/jolly", "api_key": "bad-key"}
    )

    assert resp.json()["ok"] is False
    assert resp.json()["reason"] == "unauthorized"
    assert _config()["model"]["provider"] == "openai-codex"
    from hermes_cli.config import get_env_value

    assert not get_env_value("ANRAK_JOLLY_API_KEY")


def test_sign_in_token_is_used_when_no_key(client, jolly_server):
    jolly_server["valid"].add("oauth-tok")
    token_dir = Path(os.environ["HERMES_HOME"]) / "mcp-tokens"
    token_dir.mkdir(parents=True, exist_ok=True)
    (token_dir / "Anrak_Legal.json").write_text(json.dumps({"access_token": "oauth-tok"}))

    resp = client.post("/api/providers/anrak/activate", json={"model": "anraklegal/jolly"})

    body = resp.json()
    assert body["ok"] is True, body
    assert body["source"] == "profile:anrak"
    assert _config()["model"]["provider"] == "anrak"


def test_unknown_provider_is_rejected(client):
    resp = client.post("/api/providers/not-a-provider/activate", json={"model": "m"})
    assert resp.status_code == 400
