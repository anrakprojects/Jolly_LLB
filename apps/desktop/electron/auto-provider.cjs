// Zero-config provider auto-detection for Jolly LLB.
//
// Runs auto_provider.py (a sibling file, read out of the asar and written to a
// real path so the runtime's Python can execute it) once per launch, just
// before the backend starts. It points the runtime at whichever supported login
// — Claude Code or ChatGPT/Codex — already exists on the machine, so first-time
// users never see a setup screen.
//
// Best-effort by contract: every failure mode is swallowed. Auto-config must
// NEVER block or crash app startup; if it can't help, the normal onboarding
// still runs.

const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')

/**
 * @param {object}   opts
 * @param {string}   opts.hermesHome  Resolved HERMES_HOME (~/.hermes or LOCALAPPDATA/hermes).
 * @param {string}   opts.venvPython  Absolute path to the runtime venv's python.
 * @param {(msg:string)=>void} [opts.log]  Optional logger.
 * @returns {string|null}  The AUTOCONFIG result token (e.g. 'claude'), or null.
 */
function autoConfigureProvider({ hermesHome, venvPython, log } = {}) {
  const note = typeof log === 'function' ? log : () => {}
  try {
    if (!venvPython || !fs.existsSync(venvPython)) {
      note('[auto-provider] skipped: no venv python')
      return null
    }

    const source = path.join(__dirname, 'auto_provider.py')
    let script
    try {
      script = fs.readFileSync(source, 'utf8') // asar-aware in Electron
    } catch (err) {
      note(`[auto-provider] skipped: cannot read helper (${err.message})`)
      return null
    }

    // Materialize to a real file — Python can't import/exec from inside the asar.
    const target = path.join(hermesHome, '.jolly-autoprovider.py')
    try {
      fs.mkdirSync(hermesHome, { recursive: true })
      fs.writeFileSync(target, script, 'utf8')
    } catch (err) {
      note(`[auto-provider] skipped: cannot stage helper (${err.message})`)
      return null
    }

    // Google OAuth client for the runtime's Gemini sign-in — staged at build
    // time as a git-ignored sidecar (GitHub push protection rejects the
    // literal in-repo; the values are the public gemini-cli installed-app
    // client). Missing sidecar just means the Google option needs a local
    // gemini-cli — never a launch failure.
    let geminiClient = {}
    try {
      geminiClient = JSON.parse(fs.readFileSync(path.join(__dirname, 'gemini-oauth-client.json'), 'utf8'))
    } catch { /* optional */ }

    const out = execFileSync(venvPython, [target], {
      env: {
        ...process.env,
        HERMES_HOME: hermesHome,
        JOLLY_GEMINI_CLIENT_ID: String(geminiClient.client_id || ''),
        JOLLY_GEMINI_CLIENT_SECRET: String(geminiClient.client_secret || '')
      },
      encoding: 'utf8',
      timeout: 15000,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    const result = String(out || '')
      .split('\n')
      .map(l => l.trim())
      .filter(l => l.startsWith('AUTOCONFIG='))
      .pop()
    const token = result ? result.slice('AUTOCONFIG='.length) : null
    note(`[auto-provider] ${token || 'no-result'}`)
    return token
  } catch (err) {
    // execFileSync throws on non-zero exit / timeout — never let that escape.
    note(`[auto-provider] error (ignored): ${err && err.message ? err.message : err}`)
    return null
  }
}

module.exports = { autoConfigureProvider }
