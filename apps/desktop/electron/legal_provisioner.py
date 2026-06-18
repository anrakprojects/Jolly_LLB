#!/usr/bin/env python3
"""Jolly LLB / AnrakLegal — first-run config.yaml MCP merge helper.

Run once per launch by the Electron shell (legal-provisioner.cjs) via the
runtime venv's Python — the SAME mechanism auto_provider.py uses. The .cjs side
owns the SOUL.md and SKILL.md files (plain only-if-absent writes need no Python);
this helper exists solely to merge the AnrakLegal ``mcp_servers`` entry into
``~/.hermes/config.yaml`` with a real YAML round-trip, so we never corrupt the
~600-line config a text-surgery approach would put at risk.

Contract (mirrors auto_provider.py):
  * Idempotent and best-effort. Prints a single ``LEGALCONFIG=<token>`` line and
    always exits 0. Never raises out, never blocks the launch.
  * Merges ONLY the one ``mcp_servers["Anrak Legal"]`` entry. Every other
    mcp_servers key and every other top-level config key is preserved byte-for-
    byte in value (PyYAML re-dumps the doc, but no keys are added or removed).
  * NEVER clobbers a user-edited entry: if ``mcp_servers["Anrak Legal"]`` already
    exists, we leave it ALONE (it may hold the user's real token / enabled:true).
    We only create it when absent.

Token safety (the whole point):
  The bundled template ships the entry DISABLED with a placeholder host. We do
  NOT hardcode the developer's token. The only way a live token lands here is:
    1. env ANRAK_MCP_TOKEN is set for this launch (opt-in; the SSO step can
       export it), in which case we materialize url+enabled for THIS user; or
    2. a later SSO/token-injection step rewrites the entry directly.

Inputs (argv):
  argv[1] = path to config.yaml         (HERMES_HOME/config.yaml)
  argv[2] = path to the bundled MCP template yaml (materialized by the .cjs)
Env:
  ANRAK_MCP_TOKEN (optional) — per-user token to activate the entry live.
"""

import os
import sys

# Sentinel the SSO/token-injection step looks for. Keep in sync with
# legal-mcp-template.yaml.
PLACEHOLDER_TOKEN = "__ANRAK_USER_TOKEN__"
SERVER_KEY = "Anrak Legal"


def _emit(token):
    print("LEGALCONFIG=" + token)


def _atomic_write(path, text):
    tmp = path + ".jolly-legal.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else ""
    template_path = sys.argv[2] if len(sys.argv) > 2 else ""
    if not config_path or not template_path:
        _emit("skip-no-args")
        return

    try:
        import yaml  # PyYAML ships in the runtime venv (Hermes depends on it).
    except Exception as exc:  # pragma: no cover - venv always has PyYAML
        _emit("skip-no-yaml " + repr(exc))
        return

    # The bundled entry to inject (single server config dict).
    try:
        with open(template_path, "r", encoding="utf-8") as handle:
            template = yaml.safe_load(handle) or {}
    except Exception as exc:
        _emit("skip-bad-template " + repr(exc))
        return
    tmpl_servers = template.get("mcp_servers") or {}
    entry = tmpl_servers.get(SERVER_KEY)
    if not isinstance(entry, dict):
        _emit("skip-template-empty")
        return
    entry = dict(entry)  # shallow copy we may mutate before writing

    # If a per-user token was supplied for THIS launch, activate the entry live
    # instead of provisioning it disabled. This is the ONLY in-provisioner path
    # that writes a real token, and only the user's own (from env), never the
    # dev's.
    user_token = (os.environ.get("ANRAK_MCP_TOKEN") or "").strip()
    if user_token:
        entry["url"] = entry.get("url", "").replace(PLACEHOLDER_TOKEN, user_token)
        entry["enabled"] = True

    # Load the existing config (preserve everything). Absent/empty -> start {}.
    config = {}
    existed = os.path.exists(config_path)
    if existed:
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
        except Exception as exc:
            # Don't risk rewriting a config we couldn't parse — bail clean.
            _emit("skip-unparseable-config " + repr(exc))
            return
    if not isinstance(config, dict):
        _emit("skip-config-not-mapping")
        return

    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = {}

    # CRITICAL idempotency / no-clobber: only create the entry if absent. If the
    # user (or the SSO step) already wrote one, it likely holds their real token
    # or enabled:true — never overwrite it.
    if SERVER_KEY in servers:
        _emit("present")
        return

    servers[SERVER_KEY] = entry
    config["mcp_servers"] = servers

    try:
        dumped = yaml.safe_dump(
            config,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            width=4096,
        )
        _atomic_write(config_path, dumped)
    except Exception as exc:
        _emit("skip-write-failed " + repr(exc))
        return

    _emit("merged-disabled" if not user_token else "merged-active")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never block the launch
        _emit("error " + repr(exc))
    sys.exit(0)
