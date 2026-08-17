# Jolly Anrak Desktop — Engineering TODO

Actionable pointers mapped to code. `@high` = ship blocker.

---

## 1. @high — Reliable boot + Hermes runtime stays current

**Problem:** First launch / backend boot is flaky. Dev workflow clones Hermes manually instead of the app self-healing to the pinned release.

**Pointers:**

- **Bootstrap pipeline** — `electron/bootstrap-runner.cjs` drives stage-by-stage install via `scripts/install.ps1` (or `install.sh`). Commit/branch pin comes from `install-stamp.json` written by `apps/desktop/scripts/write-build-stamp.cjs` and read in `electron/main.cjs` (`loadInstallStamp()`).
- **Backend resolution & probes** — `electron/backend-probes.cjs` (`canImportHermesCli`, `verifyHermesCli`); boot failure UI in `src/components/boot-failure-overlay.tsx` and `src/components/desktop-install-overlay.tsx`.
- **Backend/Hermes update (runtime, not shell)** — `src/store/updates.ts` (`applyUpdates`, `reportBackendContract`); IPC `hermes:updates:*` in `electron/preload.cjs`; handler in `electron/main.cjs`; API `/api/hermes/update` wired from `src/hermes.ts` → `src/app/command-center/index.tsx`.
- **Install script contract** — `scripts/install.ps1` stage protocol (`-Manifest`, `-Stage`, `-Commit`, `-Branch`); parity docs in `apps/desktop/README.md`.
- **Known gap:** bootstrap retry/cancel from renderer is deferred (see comment block at top of `bootstrap-runner.cjs`).

**Next steps:**

1. Reproduce boot failures from logs (`~/.hermes/logs/gateway.log`, desktop console).
2. Ensure packaged builds always carry a valid `install-stamp.json` and bootstrap re-runs when `ACTIVE_HERMES_ROOT` is missing or stale.
3. Wire renderer retry into `bootstrap-runner.cjs` event channel (`type: 'failed'`).
4. Align dev checkouts: stop manual clone — use `HERMES_DESKTOP_HERMES_ROOT` or the in-app update flow.

---

## 2. @high — OTA desktop updates (no full reinstall)

**Problem:** Users must reinstall to get new builds; no seamless in-place update.

**Pointers:**

- **Binary auto-update (Electron shell)** — `electron/auto-updater.cjs` (electron-updater + Azure Blob feed). Currently a **no-op** until feed URL, code-signing, and notarization are configured. Setup guide: `apps/desktop/docs/AUTO_UPDATE.md`.
- **Publish config** — `apps/desktop/package.json` (`publish`, `electron-updater` dep); post-pack hook `apps/desktop/scripts/after-pack.cjs`.
- **Backend self-update (Hermes runtime)** — separate path via `applyUpdates()` in `src/store/updates.ts`; may land on `stage: 'manual'` with `hermes update` command (`src/app/updates-overlay.tsx`).
- **Update UI** — `src/app/updates-overlay.tsx`, `$updateOverlayOpen` / `$updateStatus` in `src/store/updates.ts`.
- **Note:** auto polling is intentionally disabled (`startUpdatePoller()` in `updates.ts` — only manual check).

**Next steps:**

1. Configure real Azure Blob feed in `package.json` publish block (replace `REPLACE-ME` host).
2. Code-sign + notarize macOS builds; sign Windows builds for Squirrel.
3. Enable opt-in background check once feed is live (currently stripped in `startUpdatePoller`).
4. Unify shell binary update + backend `hermes update` into one user-facing "Update Jolly Anrak" flow.

---

## 3. @high — Install-time security posture

**Problem:** Repeated full reinstalls re-trigger Gatekeeper / SmartScreen warnings and feel unsafe to lawyers.

**Pointers:**

- **Code signing & entitlements** — `electron/entitlements.mac.plist`, `electron/entitlements.mac.inherit.plist`; signing wired through electron-builder config in `apps/desktop/package.json`.
- **Auto-updater gate** — `electron/auto-updater.cjs` lines 8–10: unsigned updates refused on macOS.
- **Secret handling** — `electron/hardening.cjs` (`encryptDesktopSecret`, sensitive file blocks); remote gateway token encryption via `safeStorage` in `electron/main.cjs`.
- **Install isolation** — bootstrap writes to `HERMES_HOME` only (`scripts/install.ps1` `-HermesHome`); no system-wide changes by default.

**Next steps:**

1. Ship signed + notarized builds through AnrakLegal release pipeline (not raw GitHub clone).
2. Document trust model for legal users (what touches disk, where secrets live).
3. Reduce reinstall frequency by fixing items 1–2 above.

---

## 4. Computer use + secure Conductor model routing

**Problem:** `computer_use` not bundled by default. ANRAK Legal Conductor API key cannot ship in the app (leak risk).

**Pointers:**

- **Computer use toolset** — upstream docs `website/docs/user-guide/features/computer-use.md`; toolset key `computer_use` in `toolsets.py`; install via `hermes computer-use install` / `hermes tools`.
- **Default toolsets for desktop** — `hermes_cli/config.py` `DEFAULT_CONFIG.tools.*`; desktop platform toolset enablement.
- **Conductor / custom provider** — model-provider plugin pattern: `plugins/model-providers/<name>/` + `website/docs/developer-guide/model-provider-plugin.md`.
- **Secret storage (do NOT embed keys)** — `electron/hardening.cjs` + `safeStorage` in `electron/main.cjs`; user secrets belong in `~/.hermes/.env` (see `hermes_cli/config.py` `OPTIONAL_ENV_VARS`). For server-side routing, proxy Conductor through AnrakLegal MCP/backend instead of client-side key.
- **MCP token pattern (reference)** — `electron/legal-provisioner.cjs` provisions MCP disabled with placeholder; activated via SSO/`ANRAK_MCP_TOKEN` env — same pattern for Conductor.

**Next steps:**

1. Add `computer_use` to desktop default enabled toolsets (or bootstrap `dep_ensure` stage in `install.ps1`).
2. Register ANRAK Conductor as a model-provider plugin; keys injected server-side or via OS keychain at runtime — never baked into asar.
3. Gate Conductor to authenticated AnrakLegal sessions (OAuth/MCP token flow).

---

## 5. OAuth for ChatGPT (Codex) and Claude — reliable provider login

**Problem:** Users always have ChatGPT Codex or Claude. Device-code OAuth exists but is flaky.

**Pointers:**

- **Zero-config auto-detect** — `electron/auto-provider.cjs` + `electron/auto_provider.py` (reads `~/.codex/auth.json`, Claude credentials; runs before backend start).
- **Onboarding OAuth UI** — `src/components/desktop-onboarding-overlay.tsx` (`device_code` flow, `ALLOWED_PROVIDER_IDS`); state in `src/store/onboarding.ts`.
- **Runtime readiness gate** — `src/lib/runtime-readiness.ts`, `src/app/session/hooks/use-prompt-actions.ts` (blocks send until credential present).
- **Provider errors** — `src/lib/provider-setup-errors.ts`.
- **Profiles SOUL editor** — `src/app/profiles/index.tsx` (separate from provider auth).

**Next steps:**

1. Collect failure modes from `auto_provider.py` logs and onboarding overlay (token refresh, stale pool entries).
2. Harden Codex device-code path: retry, clear stale `~/.hermes/auth.json` pool entries, surface actionable errors in overlay.
3. Add first-class Claude OAuth (currently auto-detect from CLI creds only).
4. Prefer OAuth over API-key onboarding for legal users (`OnboardingMode` in `onboarding.ts`).

---

## 6. ✔ Product rename: Jolly LLB → Jolly Anrak

**Status:** Done @26-08-17 10:38.

**Pointers (for remaining stragglers):**

- Search repo for `Jolly LLB`, `JOLLY_LLB`, `jolly-llb` in user-facing strings, installer URLs, and docs.

---

## 7. Legal-focused skills & toolsets curation

**Problem:** Bundled skills include non-legal items (Discord, game pass, etc.). Lawyers need a curated default + ability to add their own.

**Pointers:**

- **Skills UI** — `src/app/skills/index.tsx` (lists all skills/toolsets from backend; no legal filter yet).
- **Disable mechanism** — `agent/skill_utils.py` `get_disabled_skill_names()`; persisted in `config.yaml` `skills.disabled`; honored by `agent/prompt_builder.py` and `tools/skills_tool.py`.
- **Bundled vs optional** — `skills/` (default) vs `optional-skills/` (explicit install); see `tools/skills_hub.py`.
- **Legal skill template** — `electron/legal-skill.md` → provisioned as `anraklegal-paralegal` by `electron/legal-provisioner.cjs`.
- **Default config** — `hermes_cli/config.py` `DEFAULT_CONFIG` (add desktop/legal profile defaults for `skills.disabled` and `tools.*.disabled`).

**Next steps:**

1. Ship a `legal` profile distribution (or desktop-specific defaults) that disables non-legal bundled skills/toolsets on first provision.
2. Extend `legal-provisioner.cjs` (or install stage) to write default `skills.disabled` list.
3. Allow user-installed skills via existing `~/.hermes/skills/` + Skills UI toggle — keep bundled legal set as allowlist or opt-out list (product decision).
4. Audit `optional-skills/` categories for anything that should never appear for legal tenants.

---

## 8. Legal-centric system prompt & agent behavior

**Problem:** Prompt should emphasize legal workflows — delegation swarms, long-document handling, citation discipline.

**Pointers:**

- **Core identity** — `agent/prompt_builder.py` `DEFAULT_AGENT_IDENTITY` (already paralegal-framed).
- **SOUL override** — `electron/legal-soul.md` provisioned to `~/.hermes/SOUL.md` by `electron/legal-provisioner.cjs`; editable in `src/app/profiles/index.tsx`.
- **Paralegal skill** — `electron/legal-skill.md` (MCP tool chaining, vault-first research).
- **Delegation / swarms** — `tools/delegate_tool.py`, `delegation:` config in `hermes_cli/config.py`; guidance in `AGENTS.md` Delegation section.
- **Long documents** — context compression `agent/context_compressor.py`; file tools in `tools/` (`read_file`, `patch`); consider enabling `session_search` + legal doc skills.
- **Platform-specific prompt pieces** — `AIAgent._build_system_prompt()` in `run_agent.py` calls `prompt_builder.py` helpers.

**Next steps:**

1. Extend `legal-soul.md` with explicit delegation playbook (when to spawn subagents for research vs drafting).
2. Add legal long-doc guidance to `DEFAULT_AGENT_IDENTITY` or a dedicated `SKILLS_GUIDANCE` block in `prompt_builder.py`.
3. Default-enable `delegation` toolset for desktop/legal profile.
4. Review bundled non-legal skills index injection (`prompt_builder.py` `iter_skill_index_files`) so disabled skills never appear in prompt.
