// First-run paralegal identity provisioner for Jolly LLB.
//
// The rebranded shell bootstraps a GENERIC upstream Hermes runtime. The
// AnrakLegal paralegal identity — the SOUL, the "anraklegal-paralegal" skill,
// and the AnrakLegal MCP server — lives only in the developer's ~/.hermes and
// is NOT part of that runtime. This module ships that identity inside the app
// and lays it down into the user's HERMES_HOME on each launch, so a fresh
// install actually behaves as the paralegal instead of vanilla Hermes.
//
// What it ensures (idempotently, only-if-absent — never overwriting user edits):
//   1. ~/.hermes/SOUL.md                                   (overrides default_soul.py)
//   2. ~/.hermes/skills/anraklegal-paralegal/SKILL.md
//   3. mcp_servers["Anrak Legal"] in ~/.hermes/config.yaml (merged, DISABLED
//      with a placeholder token — see token-safety note below)
//
// Token safety: the live MCP URL is per-user (https://anrak.legal/token/<token>
// /mcp). We must NOT ship the dev's token to everyone, so the entry is
// provisioned DISABLED with a placeholder; the runtime skips enabled:false
// servers entirely (no connection, no error). The MCP/SSO flow activates it
// later by injecting the user's own token + flipping enabled:true. If a token
// is handed to THIS launch via the ANRAK_MCP_TOKEN env var, the helper fills it
// in live instead. See legal_provisioner.py and legal-mcp-template.yaml.
//
// Best-effort by contract — mirrors auto-provider.cjs: every failure mode is
// swallowed. Provisioning must NEVER block or crash app startup; if it can't
// help, the app still launches (just as generic Hermes).

const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')

const SKILL_NAME = 'anraklegal-paralegal'

/**
 * Copy a bundled asar file (sibling of this .cjs) to a real path, ONLY if the
 * destination is absent. Returns 'written' | 'present' | 'skip:<reason>'.
 * Best-effort: any error returns a 'skip:' token, never throws.
 *
 * @param {string} bundledName  Filename next to this module inside the asar.
 * @param {string} destPath     Absolute destination on the real filesystem.
 * @param {(msg:string)=>void} note
 */
function writeIfAbsent(bundledName, destPath, note) {
  try {
    if (fs.existsSync(destPath)) {
      return 'present'
    }
    const source = path.join(__dirname, bundledName)
    let content
    try {
      content = fs.readFileSync(source, 'utf8') // asar-aware in Electron
    } catch (err) {
      note(`[legal-provisioner] cannot read bundled ${bundledName} (${err.message})`)
      return 'skip:no-source'
    }
    try {
      fs.mkdirSync(path.dirname(destPath), { recursive: true })
      // wx => fail (don't overwrite) if it raced into existence since the check.
      fs.writeFileSync(destPath, content, { encoding: 'utf8', flag: 'wx' })
    } catch (err) {
      if (err && err.code === 'EEXIST') return 'present'
      note(`[legal-provisioner] cannot write ${destPath} (${err.message})`)
      return 'skip:write-failed'
    }
    return 'written'
  } catch (err) {
    note(`[legal-provisioner] writeIfAbsent error (ignored): ${err && err.message ? err.message : err}`)
    return 'skip:error'
  }
}

/**
 * Merge the AnrakLegal MCP server entry into config.yaml via the runtime venv's
 * Python + PyYAML (safe round-trip — see "why Python" in config_merge_logic).
 * Best-effort: returns the helper's LEGALCONFIG token, or null. Never throws.
 *
 * @param {object} opts
 * @param {string} opts.hermesHome
 * @param {string} opts.venvPython
 * @param {(msg:string)=>void} opts.note
 */
function mergeMcpEntry({ hermesHome, venvPython, note }) {
  try {
    if (!venvPython || !fs.existsSync(venvPython)) {
      note('[legal-provisioner] mcp merge skipped: no venv python')
      return null
    }

    // Materialize the helper script — Python can't exec from inside the asar.
    const helperSrc = path.join(__dirname, 'legal_provisioner.py')
    let helper
    try {
      helper = fs.readFileSync(helperSrc, 'utf8')
    } catch (err) {
      note(`[legal-provisioner] mcp merge skipped: cannot read helper (${err.message})`)
      return null
    }
    const helperTarget = path.join(hermesHome, '.jolly-legal-provisioner.py')

    // Materialize the bundled MCP template too (the helper reads it as data).
    const templateSrc = path.join(__dirname, 'legal-mcp-template.yaml')
    let template
    try {
      template = fs.readFileSync(templateSrc, 'utf8')
    } catch (err) {
      note(`[legal-provisioner] mcp merge skipped: cannot read template (${err.message})`)
      return null
    }
    const templateTarget = path.join(hermesHome, '.jolly-legal-mcp-template.yaml')

    try {
      fs.mkdirSync(hermesHome, { recursive: true })
      fs.writeFileSync(helperTarget, helper, 'utf8')
      fs.writeFileSync(templateTarget, template, 'utf8')
    } catch (err) {
      note(`[legal-provisioner] mcp merge skipped: cannot stage helper (${err.message})`)
      return null
    }

    const configPath = path.join(hermesHome, 'config.yaml')
    const out = execFileSync(venvPython, [helperTarget, configPath, templateTarget], {
      env: { ...process.env, HERMES_HOME: hermesHome },
      encoding: 'utf8',
      timeout: 15000,
      stdio: ['ignore', 'pipe', 'pipe']
    })
    const result = String(out || '')
      .split('\n')
      .map(l => l.trim())
      .filter(l => l.startsWith('LEGALCONFIG='))
      .pop()
    const token = result ? result.slice('LEGALCONFIG='.length) : null
    note(`[legal-provisioner] mcp ${token || 'no-result'}`)
    return token
  } catch (err) {
    // execFileSync throws on non-zero exit / timeout — never let that escape.
    note(`[legal-provisioner] mcp merge error (ignored): ${err && err.message ? err.message : err}`)
    return null
  }
}

/**
 * Ensure the AnrakLegal paralegal identity exists in HERMES_HOME. Idempotent,
 * best-effort, never throws, never blocks launch. Call once per launch BEFORE
 * autoConfigureProvider (so the runtime sees the identity on first boot).
 *
 * @param {object}   opts
 * @param {string}   opts.hermesHome  Resolved HERMES_HOME (~/.hermes or LOCALAPPDATA/hermes).
 * @param {string}   [opts.venvPython]  Absolute path to the runtime venv python (for the YAML merge).
 * @param {(msg:string)=>void} [opts.log]  Optional logger.
 * @returns {{soul:string, skill:string, mcp:(string|null)}}  Per-step result tokens.
 */
function provisionLegalIdentity({ hermesHome, venvPython, log } = {}) {
  const note = typeof log === 'function' ? log : () => {}
  const result = { soul: 'skip:no-home', skill: 'skip:no-home', mcp: null }
  try {
    if (!hermesHome) {
      note('[legal-provisioner] skipped: no hermesHome')
      return result
    }

    // 1. SOUL.md — overrides the runtime's default_soul.py when present. Only
    //    written if absent, so a user who edits their SOUL keeps it.
    result.soul = writeIfAbsent('legal-soul.md', path.join(hermesHome, 'SOUL.md'), note)

    // 2. The paralegal skill. Only written if absent.
    result.skill = writeIfAbsent(
      'legal-skill.md',
      path.join(hermesHome, 'skills', SKILL_NAME, 'SKILL.md'),
      note
    )

    // 3. The AnrakLegal MCP server entry — merged into config.yaml without
    //    clobbering other servers/keys (Python + PyYAML round-trip).
    result.mcp = mergeMcpEntry({ hermesHome, venvPython, note })

    note(`[legal-provisioner] soul=${result.soul} skill=${result.skill} mcp=${result.mcp || 'none'}`)
    return result
  } catch (err) {
    note(`[legal-provisioner] error (ignored): ${err && err.message ? err.message : err}`)
    return result
  }
}

module.exports = { provisionLegalIdentity }
