// ---------------------------------------------------------------------------
// Stage electron-updater + its full production dependency closure into
// build/updater-stage/node_modules so electron-builder ships it via
// extraResources.
//
// Why: this app's electron-builder setup does NOT auto-bundle node_modules into
// the asar (the renderer is Vite-bundled; the only other runtime module,
// node-pty, is staged the same way in stage-native-deps.cjs). A plain
// `require('electron-updater')` from the main process therefore fails in the
// packaged app. We stage a flat node_modules tree and load electron-updater
// from process.resourcesPath at runtime (see electron/auto-updater.cjs).
// ---------------------------------------------------------------------------
'use strict'

const fs = require('node:fs')
const path = require('node:path')

const DESKTOP = path.resolve(__dirname, '..')
const REPO_ROOT = path.resolve(DESKTOP, '..', '..')
const SEARCH = [path.join(DESKTOP, 'node_modules'), path.join(REPO_ROOT, 'node_modules')]
// NOTE: the staging dir must NOT be named "node_modules" — electron-builder
// filters directories with that name out of extraResources. We stage the
// packages flat here and map them *to* a node_modules dir in the bundle (see
// the extraResources `to` in package.json) so Node's sibling resolution works.
const OUT = path.join(DESKTOP, 'build', 'updater-stage')
const ROOT_PKG = 'electron-updater'

function findPkg(name) {
  for (const nm of SEARCH) {
    const p = path.join(nm, name)
    if (fs.existsSync(path.join(p, 'package.json'))) return p
  }
  return null
}

function prodDeps(dir) {
  try {
    const p = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'))
    return Object.keys(p.dependencies || {})
  } catch {
    return []
  }
}

const resolved = {}
const missing = []
const queue = [ROOT_PKG]
const seen = new Set()
while (queue.length) {
  const name = queue.shift()
  if (seen.has(name)) continue
  seen.add(name)
  const dir = findPkg(name)
  if (!dir) {
    missing.push(name)
    continue
  }
  resolved[name] = dir
  for (const d of prodDeps(dir)) if (!seen.has(d)) queue.push(d)
}

fs.rmSync(OUT, { recursive: true, force: true })
fs.mkdirSync(OUT, { recursive: true })
let copied = 0
for (const [name, dir] of Object.entries(resolved)) {
  const dst = path.join(OUT, name)
  fs.mkdirSync(path.dirname(dst), { recursive: true })
  fs.cpSync(dir, dst, { recursive: true, dereference: true })
  copied++
}

console.log(`[stage-updater] staged ${copied} packages -> ${path.relative(REPO_ROOT, OUT)}`)
if (missing.length) {
  console.error(`[stage-updater] ERROR missing from node_modules: ${missing.join(', ')}`)
  process.exit(1)
}
