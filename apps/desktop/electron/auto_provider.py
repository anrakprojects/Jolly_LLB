#!/usr/bin/env python3
"""Jolly LLB zero-config provider auto-detection.

Run once per launch by the Electron shell (auto-provider.cjs) BEFORE the runtime
backend starts. Detects whichever of the two supported logins already exists on
the machine and points the runtime at it, so a first-time user never sees a
setup screen:

  * Claude Code  -> macOS Keychain ("Claude Code-credentials") or
                    ~/.claude/.credentials.json          -> provider "anthropic"
                    (the Anthropic adapter reads the same sources directly and
                     refreshes via platform.claude.com, so we only point config
                     at it and make sure no stale ANTHROPIC_* key shadows it)
  * ChatGPT/Codex-> ~/.codex/auth.json                   -> provider "openai-codex"
                    (same OAuth client as the Codex CLI; we copy its tokens into
                     ~/.hermes/auth.json ONCE as a bootstrap and Hermes refreshes
                     them itself from then on)

Health, not presence: "logged in" is judged against the store the runtime
actually uses. For ChatGPT that is Hermes' OWN token store (~/.hermes/auth.json)
— OpenAI rotates refresh tokens, so a copy of the CLI's token dies the moment
either side refreshes, and the CLI file staying on disk proves nothing. A dead
Hermes store is only revived from the CLI file when that file changed AFTER the
death (the user re-ran `codex` login); otherwise we would just replay the same
consumed refresh token.

Preference order: Claude first, then ChatGPT. When the configured primary is
dead and the other login is healthy, the primary is switched so the app keeps
working. Fully idempotent and best-effort — if anything goes wrong, the existing
config is left untouched and the launch is never blocked.

Cross-platform: the Keychain probe is macOS-only; Windows and Linux use the
credential files (both CLIs keep the same paths under %USERPROFILE% / $HOME).
"""

import datetime
import json
import os
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.join(HOME, ".hermes")
CONFIG_PATH = os.path.join(HERMES_HOME, "config.yaml")
AUTH_PATH = os.path.join(HERMES_HOME, "auth.json")
ENV_PATH = os.path.join(HERMES_HOME, ".env")
CLAUDE_CREDS = os.path.join(HOME, ".claude", ".credentials.json")
CODEX_CREDS = os.path.join(HOME, ".codex", "auth.json")
CODEX_CONFIG = os.path.join(HOME, ".codex", "config.toml")

CLAUDE_MODEL = "claude-opus-4-8"
CLAUDE_BASE_URL = "https://api.anthropic.com"
CODEX_MODEL_DEFAULT = "gpt-5.5"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
# Models the ChatGPT/Codex backend rejects for ChatGPT-subscription accounts
# (HTTP 400 "not supported when using Codex with a ChatGPT account"). Never
# write these; heal them if a previous version left one in config.yaml.
CODEX_REJECTED_MODELS = ("gpt-5.3-codex",)
# Claude subscriptions ration the big models hardest: Opus quota runs out
# ("You're out of extra usage") while Sonnet — and almost always Haiku —
# still serve. The fallback chain is therefore a LADDER down the same
# subscription, not just a jump to the other provider, so a quota-gated
# Opus degrades gracefully instead of dead-ending the chat.
CLAUDE_DOWNGRADE_MODELS = ("claude-sonnet-5", "claude-haiku-4-5-20251001")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def codex_model():
    """The user's own Codex CLI model (~/.codex/config.toml) when it is usable
    for a ChatGPT account, else the known-good default."""
    try:
        with open(CODEX_CONFIG, "r", encoding="utf-8") as handle:
            text = handle.read()
        match = re.search(r'^model\s*=\s*"([^"\n]+)"', text, re.MULTILINE)
        if match:
            model = match.group(1).strip()
            if model and model not in CODEX_REJECTED_MODELS:
                return model
    except Exception:
        pass
    return CODEX_MODEL_DEFAULT


def _claude_keychain():
    """Claude Code >= 2.1.114 on macOS stores credentials in the Keychain, not
    the file — same probe the runtime's anthropic adapter uses."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password",
             "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        blob = (json.loads(result.stdout.strip() or "{}")).get("claudeAiOauth") or {}
        if blob.get("accessToken") or blob.get("refreshToken"):
            return blob
    except Exception:
        return None
    return None


def claude_login():
    """Return the claudeAiOauth blob if a usable Claude Code login exists.

    Keychain first (macOS), then the JSON file (Windows/Linux and older macOS
    installs) — the same order the runtime resolves at call time, so detection
    matches what the runtime can actually use. An expired access token with no
    refresh token is treated as logged out.
    """
    blob = _claude_keychain()
    if blob:
        return blob
    blob = (_read_json(CLAUDE_CREDS) or {}).get("claudeAiOauth") or {}
    if not (blob.get("accessToken") or blob.get("refreshToken")):
        return None
    expires_at = blob.get("expiresAt") or 0
    try:
        expired = bool(expires_at) and int(expires_at) <= int(time.time() * 1000)
    except Exception:
        expired = False
    if expired and not blob.get("refreshToken"):
        return None
    return blob


def codex_cli_login():
    """Return the ~/.codex/auth.json contents if the Codex CLI has tokens."""
    data = _read_json(CODEX_CREDS) or {}
    tokens = data.get("tokens") or {}
    if tokens.get("access_token") and tokens.get("refresh_token"):
        return data
    return None


def hermes_codex_state():
    """Health of Hermes' OWN Codex auth — the stores the runtime resolves.

    Returns (status, died_at): status is "ok" (usable tokens, no relogin flag),
    "dead" (tokens missing/stripped or relogin_required), or "absent" (never
    authenticated). died_at is the ISO timestamp of the recorded auth failure,
    when there is one. Checks the singleton (providers.openai-codex) first,
    then the credential pool — dashboard sign-ins persist there, and the
    runtime resolves pool entries when the singleton has nothing usable, so a
    live pool entry means ChatGPT works even with a dead singleton copy.
    """
    auth = _read_json(AUTH_PATH) or {}
    state = (auth.get("providers") or {}).get("openai-codex")
    if not isinstance(state, dict):
        singleton = "absent"
        died_at = None
    else:
        error = state.get("last_auth_error") or {}
        died_at = error.get("at")
        tokens = state.get("tokens") or {}
        if not (tokens.get("access_token") and tokens.get("refresh_token")):
            singleton = "dead"
        elif error.get("relogin_required"):
            singleton = "dead"
        else:
            singleton = "ok"
    if singleton == "ok":
        return "ok", died_at
    for entry in (auth.get("credential_pool") or {}).get("openai-codex") or []:
        if (
            isinstance(entry, dict)
            and entry.get("access_token")
            and entry.get("refresh_token")
            and entry.get("last_status") != "dead"
        ):
            return "ok", died_at
    return singleton, died_at


def codex_usable():
    """Resolve ChatGPT auth against the store the runtime reads.

    Returns (status, cli_data):
      "ok"     — Hermes' store is healthy; nothing to import.
      "import" — Hermes' store is absent/dead but the CLI file can seed it
                 (first run, or the user re-ran `codex` login after the death).
      "none"   — no usable ChatGPT auth anywhere.
    """
    status, died_at = hermes_codex_state()
    if status == "ok":
        return "ok", None
    cli = codex_cli_login()
    if not cli:
        return "none", None
    if died_at:
        # Only revive from a CLI file written AFTER Hermes' copy died —
        # re-importing an older file replays the already-consumed refresh token.
        try:
            died = datetime.datetime.fromisoformat(
                str(died_at).replace("Z", "+00:00")
            ).timestamp()
            if os.path.getmtime(CODEX_CREDS) <= died:
                return "none", None
        except Exception:
            return "none", None
    return "import", cli


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


def heal_rejected_codex_models(text, model):
    """Swap any known-rejected Codex model id left by earlier versions for the
    resolved one, wherever it appears (model block or fallback entries)."""
    for bad in CODEX_REJECTED_MODELS:
        text = text.replace(bad, model)
    return text


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


def write_codex_auth(codex, make_active):
    """Copy the Codex CLI tokens into Hermes' auth store (same OAuth client, so
    Hermes refreshes them independently from then on). Replacing the provider
    entry also clears any recorded last_auth_error. active_provider is only
    flipped when ChatGPT becomes the primary — importing tokens for fallback
    use must not steal the active slot."""
    auth = _read_json(AUTH_PATH) or {}
    providers = auth.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        auth["providers"] = providers
    providers["openai-codex"] = {
        "tokens": codex.get("tokens"),
        "last_refresh": codex.get("last_refresh"),
    }
    if make_active:
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


def fallback_ladder(primary, claude, codex, chatgpt_model):
    """Ordered fallback chain for the given primary provider.

    Claude primary:  Sonnet -> ChatGPT (if healthy) -> Haiku. The downgrades
    share the primary's login, so even a single-provider user survives an
    Opus quota gate. ChatGPT primary: Opus -> Sonnet -> Haiku (needs Claude).
    The runtime walks this list in order and skips entries matching the
    failing provider+model, so same-provider downgrades are safe.
    """
    entries = []
    if primary == "anthropic":
        entries.append({"provider": "anthropic", "model": CLAUDE_DOWNGRADE_MODELS[0]})
        if codex:
            entries.append({"provider": "openai-codex", "model": chatgpt_model})
        for model in CLAUDE_DOWNGRADE_MODELS[1:]:
            entries.append({"provider": "anthropic", "model": model})
    else:
        if claude:
            entries.append({"provider": "anthropic", "model": CLAUDE_MODEL})
            for model in CLAUDE_DOWNGRADE_MODELS:
                entries.append({"provider": "anthropic", "model": model})
    return entries


def main():
    text = read_config()
    provider = current_provider(text)
    claude = claude_login()
    codex_status, codex_cli = codex_usable()
    codex = codex_status != "none"
    chatgpt_model = codex_model()

    # Idempotent: already on a supported provider whose auth is HEALTHY — don't
    # touch the primary. Still wire the cross-provider fallback if the other
    # login exists and no fallback is configured yet (never clobber a
    # deliberate one), and heal any known-rejected Codex model id.
    if provider == "anthropic" and claude:
        healed = heal_rejected_codex_models(text, chatgpt_model)
        ladder = fallback_ladder("anthropic", claude, codex, chatgpt_model)
        if fallback_is_empty(healed) and ladder:
            if codex_status == "import":
                write_codex_auth(codex_cli, make_active=False)
            healed = replace_fallback_block(healed, ladder)
            _atomic_write(CONFIG_PATH, healed)
            print("AUTOCONFIG=skip-claude+fallback-ladder")
        else:
            if healed != text:
                _atomic_write(CONFIG_PATH, healed)
            print("AUTOCONFIG=skip-claude")
        return
    if provider == "openai-codex" and codex:
        if codex_status == "import":
            # Revive Hermes' dead/absent store from a fresher CLI login.
            write_codex_auth(codex_cli, make_active=True)
        healed = heal_rejected_codex_models(text, chatgpt_model)
        ladder = fallback_ladder("openai-codex", claude, codex, chatgpt_model)
        if fallback_is_empty(healed) and ladder:
            healed = replace_fallback_block(healed, ladder)
            _atomic_write(CONFIG_PATH, healed)
            print("AUTOCONFIG=skip-chatgpt+fallback-ladder")
        else:
            if healed != text:
                _atomic_write(CONFIG_PATH, healed)
            print("AUTOCONFIG=skip-chatgpt")
        return

    # The configured primary is dead or unsupported. Prefer Claude, then
    # ChatGPT. Whichever is primary, the OTHER login (if healthy) is wired as an
    # automatic fallback so a quota-exhausted subscription transparently spills
    # over instead of erroring.
    if claude:
        text = replace_model_block(text, "anthropic", CLAUDE_MODEL, CLAUDE_BASE_URL)
        if codex_status == "import":
            write_codex_auth(codex_cli, make_active=False)
        text = replace_fallback_block(
            text, fallback_ladder("anthropic", claude, codex, chatgpt_model)
        )
        text = heal_rejected_codex_models(text, chatgpt_model)
        _atomic_write(CONFIG_PATH, text)
        clear_env_keys(["ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN"])
        switched = "+switched" if provider == "openai-codex" else ""
        print("AUTOCONFIG=claude+fallback-ladder" + switched)
        return
    if codex:
        if codex_status == "import":
            write_codex_auth(codex_cli, make_active=True)
        text = replace_model_block(text, "openai-codex", chatgpt_model, CODEX_BASE_URL)
        text = replace_fallback_block(
            text, fallback_ladder("openai-codex", claude, codex, chatgpt_model)
        )
        text = heal_rejected_codex_models(text, chatgpt_model)
        _atomic_write(CONFIG_PATH, text)
        switched = "+switched" if provider == "anthropic" else ""
        print("AUTOCONFIG=chatgpt" + switched)
        return

    # Neither login is usable. If a supported provider is configured, its auth
    # has died — the runtime's setup.runtime_check will report it and the shell
    # surfaces the sign-in screen; we just name the state for the logs.
    if provider in ("anthropic", "openai-codex"):
        print("AUTOCONFIG=relogin-required")
    else:
        print("AUTOCONFIG=none")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never block the launch
        print("AUTOCONFIG=error " + repr(exc))
