'use strict'

/**
 * Copy this checkout into apps/desktop/build/bundled-runtime so packaged
 * installers ship the same Python runtime `npm run dev` uses — not a GitHub
 * clone of a private repo, and not whatever `hermes` happens to be on PATH.
 *
 * Called from `npm run build`. electron-builder then packs the folder via
 * extraResources → resources/bundled-runtime.
 *
 * The copy destination lives inside the repo, so we stage through os.tmpdir()
 * first. Node's fs.cpSync refuses to copy a tree into a subdirectory of itself.
 */

const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const APP_ROOT = path.resolve(__dirname, '..')
const REPO_ROOT = path.resolve(APP_ROOT, '..', '..')
const OUT_DIR = path.join(APP_ROOT, 'build', 'bundled-runtime')

const SKIP_DIR_NAMES = new Set([
  '.git',
  '.github',
  '.cursor',
  '.plans',
  '.pytest_cache',
  '.mypy_cache',
  '.ruff_cache',
  '.tox',
  '.venv',
  '__pycache__',
  'coverage',
  'docker',
  'node_modules',
  'nix',
  'tests',
  'venv',
  'website'
])

const SKIP_TOP_LEVEL = ['tests', 'website', '.github', 'docker', 'nix', '.plans', 'docs', 'infographic']

function skipRelative(rel) {
  if (!rel || rel === '.') return false
  const parts = rel.replace(/\\/g, '/').split('/').filter(part => part && part !== '.')
  if (parts[0] === '..') return false
  if (parts.some(part => SKIP_DIR_NAMES.has(part))) return true
  // Electron UI is already packed into the asar; don't nest it in the runtime.
  if (parts[0] === 'apps' && parts[1] === 'desktop') return true
  return false
}

function main() {
  if (!fs.existsSync(path.join(REPO_ROOT, 'pyproject.toml'))) {
    console.error('[stage-bundled-runtime] ERROR: pyproject.toml missing at', REPO_ROOT)
    process.exit(1)
  }
  if (!fs.existsSync(path.join(REPO_ROOT, 'scripts', 'install.ps1'))) {
    console.error('[stage-bundled-runtime] ERROR: scripts/install.ps1 missing at', REPO_ROOT)
    process.exit(1)
  }

  fs.mkdirSync(path.join(APP_ROOT, 'build', 'bundled-node'), { recursive: true })

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'jolly-bundled-'))
  try {
    fs.cpSync(REPO_ROOT, tmpDir, {
      recursive: true,
      dereference: true,
      filter: src => {
        const rel = path.relative(path.resolve(REPO_ROOT), path.resolve(src))
        return !skipRelative(rel)
      }
    })

    fs.rmSync(OUT_DIR, { recursive: true, force: true })
    fs.mkdirSync(path.dirname(OUT_DIR), { recursive: true })
    fs.cpSync(tmpDir, OUT_DIR, { recursive: true, dereference: true })
    for (const name of SKIP_TOP_LEVEL) {
      fs.rmSync(path.join(OUT_DIR, name), { recursive: true, force: true })
    }
    fs.rmSync(path.join(OUT_DIR, 'apps', 'desktop'), { recursive: true, force: true })
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true })
  }

  const stagedPyproject = path.join(OUT_DIR, 'pyproject.toml')
  const stagedInstall = path.join(OUT_DIR, 'scripts', process.platform === 'win32' ? 'install.ps1' : 'install.sh')
  if (!fs.existsSync(stagedPyproject) || !fs.existsSync(stagedInstall)) {
    console.error('[stage-bundled-runtime] ERROR: staged runtime is missing', stagedPyproject, 'or', stagedInstall)
    process.exit(1)
  }

  console.log('[stage-bundled-runtime] wrote', path.relative(REPO_ROOT, OUT_DIR))
}

main()
