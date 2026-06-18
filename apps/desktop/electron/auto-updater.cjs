// ---------------------------------------------------------------------------
// Binary auto-update via electron-updater against a generic feed (Azure Blob).
//
// This is intentionally a COMPLETE NO-OP until all three are true:
//   1. the app is packaged (not a dev run),
//   2. a real feed URL is baked into app-update.yml (electron-builder writes it
//      from the `publish` config in package.json — placeholder host is ignored),
//   3. the build is code-signed + notarized (macOS Squirrel.Mac refuses to
//      apply unsigned updates; this is the same Apple Developer cert that
//      removes the Gatekeeper warning).
//
// It is designed to NEVER crash the app: every path is guarded and failures
// only write to the desktop log. Safe to ship in unsigned test builds — it just
// stays dormant.
//
// See docs/AUTO_UPDATE.md for the full setup + publish flow.
// ---------------------------------------------------------------------------
'use strict'

const fs = require('node:fs')
const path = require('node:path')
const { app, dialog } = require('electron')

// If the feed URL still points at this host, the publisher hasn't configured a
// real Azure Blob container yet — stay dormant.
const PLACEHOLDER_HOST = 'REPLACE-ME'
const FIRST_CHECK_DELAY_MS = 10_000 // 10s after launch
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000 // every 6h

/**
 * Read the feed URL electron-builder baked into app-update.yml. We read it only
 * to decide whether a real feed is configured; electron-updater reads the same
 * file itself for the actual checks.
 */
function readFeedUrl() {
  try {
    const ymlPath = path.join(process.resourcesPath, 'app-update.yml')
    const text = fs.readFileSync(ymlPath, 'utf8')
    const match = text.match(/^\s*url:\s*(.+)\s*$/m)
    return match ? match[1].trim().replace(/^["']|["']$/g, '') : ''
  } catch {
    return ''
  }
}

/**
 * Initialize auto-update. Returns silently (no throw) in every failure mode.
 * @param {{ log?: (line: string) => void }} [opts]
 */
function initAutoUpdater({ log } = {}) {
  const rememberLog = typeof log === 'function' ? log : () => {}
  try {
    if (!app.isPackaged) {
      rememberLog('[updater] skipped: dev run (not packaged)')
      return
    }
    if (process.env.HERMES_DISABLE_AUTO_UPDATE === '1') {
      rememberLog('[updater] skipped: HERMES_DISABLE_AUTO_UPDATE=1')
      return
    }
    const feed = readFeedUrl()
    if (!feed || feed.includes(PLACEHOLDER_HOST)) {
      rememberLog(`[updater] dormant: no real feed configured (url=${feed || 'none'})`)
      return
    }

    // electron-updater is shipped as a staged resource (this app's
    // electron-builder setup doesn't bundle node_modules into the asar — see
    // scripts/stage-updater.cjs). Load it from there; fall back to a normal
    // require for `npm start` dev runs.
    let autoUpdater
    try {
      const staged = path.join(process.resourcesPath || '', 'updater-deps', 'node_modules', 'electron-updater')
      const entry = fs.existsSync(staged) ? staged : 'electron-updater'
      ;({ autoUpdater } = require(entry))
    } catch (err) {
      rememberLog(`[updater] electron-updater unavailable: ${err && err.message}`)
      return
    }

    autoUpdater.autoDownload = true
    autoUpdater.autoInstallOnAppQuit = true
    autoUpdater.logger = {
      info: m => rememberLog(`[updater] ${m}`),
      warn: m => rememberLog(`[updater] WARN ${m}`),
      error: m => rememberLog(`[updater] ERROR ${m}`),
      debug: () => {}
    }

    autoUpdater.on('error', err =>
      rememberLog(`[updater] error: ${err == null ? 'unknown' : err.stack || err.message || String(err)}`)
    )
    autoUpdater.on('checking-for-update', () => rememberLog('[updater] checking for update'))
    autoUpdater.on('update-available', info => rememberLog(`[updater] update available: ${info && info.version}`))
    autoUpdater.on('update-not-available', () => rememberLog('[updater] up to date'))
    autoUpdater.on('download-progress', p => rememberLog(`[updater] downloading ${Math.round(p.percent)}%`))
    autoUpdater.on('update-downloaded', info => {
      const version = (info && info.version) || ''
      rememberLog(`[updater] downloaded ${version}; prompting to restart`)
      dialog
        .showMessageBox({
          type: 'info',
          buttons: ['Restart now', 'Later'],
          defaultId: 0,
          cancelId: 1,
          title: 'Update ready',
          message: `A new version of ${app.getName()}${version ? ` (${version})` : ''} has been downloaded.`,
          detail: 'Restart to install it now. It will also install automatically next time you quit.'
        })
        .then(result => {
          if (result.response === 0) {
            try {
              autoUpdater.quitAndInstall()
            } catch (e) {
              rememberLog(`[updater] quitAndInstall failed: ${e && e.message}`)
            }
          }
        })
        .catch(() => {})
    })

    const check = () => {
      try {
        autoUpdater.checkForUpdates().catch(err => rememberLog(`[updater] check failed: ${err && err.message}`))
      } catch (err) {
        rememberLog(`[updater] check threw: ${err && err.message}`)
      }
    }

    setTimeout(check, FIRST_CHECK_DELAY_MS)
    setInterval(check, CHECK_INTERVAL_MS)
    rememberLog(`[updater] initialized against feed ${feed}`)
  } catch (err) {
    rememberLog(`[updater] init failed: ${err && err.message ? err.message : String(err)}`)
  }
}

module.exports = { initAutoUpdater }
