# Auto-update (electron-updater + Azure Blob)

Installed Jolly LLB desktop apps check an Azure Blob feed on launch, download a
newer signed build, and install it on restart — so you ship changes from your
end without anyone reinstalling.

## How it works

```
build (signed)  ──►  release/*.dmg, *.zip, *.blockmap, latest-mac.yml
       │
       └── scripts/publish-azure.sh ──►  Azure Blob container (jolly-updates)
                                                │
installed app (electron-updater) ──checks──────►│  reads latest-mac.yml,
                                                 │  downloads the new build,
                                                 ◄──  installs on restart
```

- **Updater module:** `electron/auto-updater.cjs` — initialized from `main.cjs`
  after the window opens. It checks 10s after launch, then every 6h, and prompts
  to restart when an update is downloaded.
- **Feed config:** `publish` block in `package.json` (`provider: generic`,
  `url:` = your Blob container). electron-builder bakes this into
  `app-update.yml` inside the app and emits `latest-mac.yml` at build time.
- **Dormant by default:** the updater is a complete no-op until the app is
  packaged, the feed `url` is real (not the `REPLACE-ME` placeholder), and the
  build is signed. So unsigned test builds are unaffected.

## One-time setup

### 1. Apple Developer cert (REQUIRED for macOS)
macOS Squirrel.Mac **refuses unsigned updates** — auto-update cannot work on a
mac without a signed + notarized build. This is the same cert that removes the
Gatekeeper warning. Set these at build time (electron-builder reads them):

| Var | Purpose |
|-----|---------|
| `CSC_LINK` / `CSC_KEY_PASSWORD` | Developer ID Application cert (.p12) |
| `APPLE_ID` / `APPLE_APP_SPECIFIC_PASSWORD` / `APPLE_TEAM_ID` | notarization |

(Windows NSIS can auto-update unsigned, but SmartScreen warns until you sign with `WIN_CSC_LINK`/`WIN_CSC_KEY_PASSWORD`.)

### 2. Azure Blob container
- Create a container (default name `jolly-updates`) in a storage account.
- Give it **Blob (anonymous read)** access, **or** append a read-only SAS to the
  feed URL so the app can fetch updates.
- Put the container URL in `package.json` → `build.publish[0].url`, replacing the
  `REPLACE-ME` placeholder:
  ```
  https://<account>.blob.core.windows.net/jolly-updates/
  ```

## Release flow

```bash
# 1. Bump the version (electron-updater compares semver)
#    edit "version" in apps/desktop/package.json, e.g. 0.15.1 -> 0.15.2

# 2. Build a SIGNED build (signing env vars must be present)
npm run dist:mac          # or dist:win / dist:linux

# 3. Publish artifacts to the Blob feed
export AZURE_STORAGE_ACCOUNT=<account>
export AZURE_STORAGE_SAS_TOKEN=<write-sas>   # or AZURE_STORAGE_KEY, or `az login`
npm run publish:azure
# (npm run release:mac does step 2 + 3 together)
```

Installed apps pick it up within ~6h, or on next launch.

## Testing without signing

You can verify the *plumbing* (feed reachable, manifest parsed) by pointing a
local build at the feed and watching `~/.hermes/logs/desktop.log` for
`[updater]` lines — but the actual install step will fail on macOS until the
build is signed. To temporarily silence the updater: set
`HERMES_DISABLE_AUTO_UPDATE=1`.

## Files

- `electron/auto-updater.cjs` — the updater (guarded, dormant-by-default)
- `scripts/publish-azure.sh` — uploads `release/` artifacts to Blob
- `package.json` → `build.publish` — the feed URL electron-builder bakes in
- npm scripts: `publish:azure`, `release:mac`
