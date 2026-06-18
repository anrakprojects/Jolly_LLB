#!/usr/bin/env python3
"""Jolly LLB zero-config provider auto-detection.

Run once per launch by the Electron shell (auto-provider.cjs) BEFORE the runtime
backend starts. Detects whichever of the two supported logins already exists on
the machine and points the runtime at it, so a first-time user never sees a
setup screen:

  * Claude Code  -> ~/.claude/.credentials.json   -> provider "anthropic"
                    (the Anthropic adapter reads that file directly and refreshes
                     it via platform.claude.com, so we only point config at it and
                     make sure no stale ANTHROPIC_* key shadows it)
  * ChatGPT/Codex-> ~/.codex/auth.json            -> provider "openai-codex"
                    (same OAuth client as the Codex CLI, so we copy its tokens
                     into ~/.hermes/auth.json and Hermes refreshes them itself)

Preference order: Claude first, then ChatGPT. Fully idempotent and best-effort —
if the runtime is already on a supported provider that still has a local login,
or if neither login exists, or if anything goes wrong, the existing config is
left untouched and the launch is never blocked.
"""

import json
import os
import re
import sys

HOME = os.path.expanduser("~")
HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.join(HOME, ".hermes")
CONFIG_PATH = os.path.join(HERMES_HOME, "config.yaml")
AUTH_PATH = os.path.join(HERMES_HOME, "auth.json")
ENV_PATH = os.path.join(HERMES_HOME, ".env")
CLAUDE_CREDS = os.path.join(HOME, ".claude", ".credentials.json")
CODEX_CREDS = os.path.join(HOME, ".codex", "auth.json")

# Model defaults. claude-opus-4-8 is the current Anthropic default; gpt-5.3-codex
# is Hermes' own Codex fallback (hermes_cli/cli.py). The user can change either
# from the in-app picker afterward.
CLAUDE_MODEL = "claude-opus-4-8"
CLAUDE_BASE_URL = "https://api.anthropic.com"
CODEX_MODEL = "gpt-5.3-codex"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def claude_login():
    """Return the claudeAiOauth blob if Claude Code is logged in, else None."""
    blob = (_read_json(CLAUDE_CREDS) or {}).get("claudeAiOauth") or {}
    if blob.get("accessToken") or blob.get("refreshToken"):
        return blob
    return None


def codex_login():
    """Return the ~/.codex/auth.json contents if ChatGPT/Codex is logged in."""
    data = _read_json(CODEX_CREDS) or {}
    tokens = data.get("tokens") or {}
    if tokens.get("access_token") and tokens.get("refresh_token"):
        return data
    return None


def read_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception:
        return ""


def current_provider(text):
    """Best-effort read of model.provider without a YAML parser."""
    match = re.search(r"^model:[ \t]*\n((?:[ \t]+.*\n?)*)", text, re.MULTILINE)
    block = match.group(1) if match else ""
    prov = re.search(r"^[ \t]+provider:[ \t]*(\S+)", block, re.MULTILINE)
    return prov.group(1).strip() if prov else None


def _atomic_write(path, content):
    tmp = path + ".jolly.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def replace_model_block(text, provider, default, base_url):
    """Return text with ONLY the top-level model: block replaced; everything
    else (mcp_servers, etc.) is preserved. Prepends one if absent."""
    block = (
        "model:\n"
        "  default: " + default + "\n"
        "  provider: " + provider + "\n"
        "  base_url: " + base_url + "\n"
    )
    pattern = re.compile(r"^model:[ \t]*\n(?:[ \t]+.*\n?)*", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(block, text, count=1)
    return block + text


def replace_fallback_block(text, entries):
    """Return text with the top-level fallback_providers: list set to `entries`
    (a list of {provider, model} dicts). When the primary provider hits its
    usage cap / rate limit, the runtime fails over to these — so a quota-limited
    Claude subscription never dead-ends; it spills to ChatGPT (and vice versa)."""
    if entries:
        block = "fallback_providers:\n"
        for entry in entries:
            block += (
                "- provider: " + entry["provider"] + "\n"
                "  model: " + entry["model"] + "\n"
            )
    else:
        block = "fallback_providers: []\n"
    # Match the fallback_providers: line plus any following list/indented lines.
    pattern = re.compile(r"^fallback_providers:.*\n(?:(?:[ \t].*|-.*)\n)*", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(block, text, count=1)
    sep = "" if text.endswith("\n") or not text else "\n"
    return text + sep + block


def clear_env_keys(keys):
    """Drop ANTHROPIC_API_KEY / ANTHROPIC_TOKEN so the adapter falls back to the
    live Claude Code credential store instead of a stale shadow key."""
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except Exception:
        return
    kept = []
    for line in lines:
        stripped = line.lstrip()
        if any(
            stripped.startswith(k + "=") or stripped.startswith("export " + k + "=")
            for k in keys
        ):
            continue
        kept.append(line)
    _atomic_write(ENV_PATH, "\n".join(kept) + ("\n" if kept else ""))


def write_codex_auth(codex):
    """Copy the Codex CLI tokens into Hermes' auth store (same OAuth client, so
    Hermes can refresh them independently)."""
    auth = _read_json(AUTH_PATH) or {}
    providers = auth.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        auth["providers"] = providers
    providers["openai-codex"] = {
        "tokens": codex.get("tokens"),
        "last_refresh": codex.get("last_refresh"),
    }
    auth["active_provider"] = "openai-codex"
    _atomic_write(AUTH_PATH, json.dumps(auth, indent=2))


def fallback_is_empty(text):
    """True if there is no top-level fallback_providers list configured yet."""
    match = re.search(
        r"^fallback_providers:(.*)\n((?:(?:[ \t].*|-.*)\n)*)", text, re.MULTILINE
    )
    if not match:
        return True
    return match.group(1).strip() in ("", "[]") and not match.group(2).strip()


def main():
    text = read_config()
    provider = current_provider(text)
    claude = claude_login()
    codex = codex_login()

    # Idempotent: already on a supported provider that still has a local login —
    # don't touch the primary. But still wire the cross-provider fallback if the
    # other login exists and no fallback is configured yet (never clobber a
    # deliberate one), so existing installs gain spill-over resilience too.
    if provider == "anthropic" and claude:
        if codex and fallback_is_empty(text):
            text = replace_fallback_block(
                text, [{"provider": "openai-codex", "model": CODEX_MODEL}]
            )
            _atomic_write(CONFIG_PATH, text)
            print("AUTOCONFIG=skip-claude+chatgpt-fallback")
        else:
            print("AUTOCONFIG=skip-claude")
        return
    if provider == "openai-codex" and codex:
        if claude and fallback_is_empty(text):
            text = replace_fallback_block(
                text, [{"provider": "anthropic", "model": CLAUDE_MODEL}]
            )
            _atomic_write(CONFIG_PATH, text)
            print("AUTOCONFIG=skip-chatgpt+claude-fallback")
        else:
            print("AUTOCONFIG=skip-chatgpt")
        return

    # Prefer Claude, then ChatGPT. (Anything else — openrouter, nous, unset — is
    # migrated to a supported login when one is available.) Whichever is primary,
    # the OTHER login (if present) is wired as an automatic fallback so a
    # quota-exhausted subscription transparently spills over instead of erroring.
    if claude:
        text = replace_model_block(text, "anthropic", CLAUDE_MODEL, CLAUDE_BASE_URL)
        fallback = (
            [{"provider": "openai-codex", "model": CODEX_MODEL}] if codex else []
        )
        text = replace_fallback_block(text, fallback)
        _atomic_write(CONFIG_PATH, text)
        clear_env_keys(["ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"])
        print("AUTOCONFIG=claude" + ("+chatgpt-fallback" if fallback else ""))
        return
    if codex:
        write_codex_auth(codex)
        text = replace_model_block(text, "openai-codex", CODEX_MODEL, CODEX_BASE_URL)
        fallback = (
            [{"provider": "anthropic", "model": CLAUDE_MODEL}] if claude else []
        )
        text = replace_fallback_block(text, fallback)
        _atomic_write(CONFIG_PATH, text)
        print("AUTOCONFIG=chatgpt" + ("+claude-fallback" if fallback else ""))
        return

    print("AUTOCONFIG=none")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never block the launch
        print("AUTOCONFIG=error " + repr(exc))
    sys.exit(0)
