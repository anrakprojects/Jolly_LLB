"""apps/desktop/electron/auto_provider.py keeps Anrak Jolly as primary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "apps" / "desktop" / "electron" / "auto_provider.py"


def _run(tmp_path: Path, env_extra: dict | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANRAK_")}
    env.update({"HOME": str(tmp_path), "HERMES_HOME": str(tmp_path / ".hermes")})
    env.update(env_extra or {})
    out = subprocess.run(
        [sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30, check=True
    ).stdout
    return next(line for line in out.splitlines() if line.startswith("AUTOCONFIG="))


@pytest.fixture
def home(tmp_path):
    if sys.platform == "darwin":
        pytest.skip("auto_provider probes the real macOS Keychain for Claude Code")
    hermes = tmp_path / ".hermes"
    hermes.mkdir()
    (hermes / "config.yaml").write_text("model:\n  default: anraklegal/jolly\n  provider: anrak\n")
    return tmp_path


def test_jolly_primary_with_api_key_is_kept(home):
    (home / ".hermes" / ".env").write_text("ANRAK_JOLLY_API_KEY=k\n")
    assert _run(home) == "AUTOCONFIG=skip-jolly"
    assert "provider: anrak" in (home / ".hermes" / "config.yaml").read_text()


def test_jolly_primary_with_sign_in_token_is_kept(home):
    tokens = home / ".hermes" / "mcp-tokens"
    tokens.mkdir()
    (tokens / "Anrak_Legal.json").write_text(json.dumps({"refresh_token": "rt"}))
    assert _run(home) == "AUTOCONFIG=skip-jolly"


def test_jolly_gets_chatgpt_fallback_ladder(home):
    (home / ".hermes" / ".env").write_text("ANRAK_JOLLY_API_KEY=k\n")
    codex = home / ".codex"
    codex.mkdir()
    (codex / "auth.json").write_text(
        json.dumps(
            {
                "tokens": {"access_token": "a", "refresh_token": "r", "id_token": "i"},
                "last_refresh": "2026-09-01T00:00:00Z",
            }
        )
    )
    assert _run(home) == "AUTOCONFIG=skip-jolly+fallback-ladder"
    config = (home / ".hermes" / "config.yaml").read_text()
    primary, fallbacks = config.split("fallback_providers:", 1)
    assert "provider: anrak" in primary
    assert "provider: openai-codex" in fallbacks


def test_jolly_without_credentials_needs_relogin(home):
    assert _run(home) == "AUTOCONFIG=relogin-required"
