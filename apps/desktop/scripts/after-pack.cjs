/**
 * after-pack.cjs — electron-builder afterPack hook.
 *
 * Stamps the Hermes icon + identity onto the packed Windows Hermes.exe via
 * rcedit (delegated to set-exe-identity.cjs). This runs for EVERY packed build
 * — first install, `hermes desktop`, the installer's --update rebuild, and a
 * dev's manual `npm run pack` — so the branded exe can never silently revert
 * to the stock "Electron" icon/name (the bug when the stamp lived only in
 * install.ps1, which the update path doesn't use).
 *
 * Windows-only: rcedit edits PE resources, irrelevant on macOS/Linux where the
 * app identity comes from the bundle Info.plist / desktop entry. Best-effort:
 * a stamp failure must never fail an otherwise-good build (worst case is the
 * stock icon, not a broken app), so we log and resolve rather than throw.
 *
 * electron-builder passes a context with:
 *   - electronPlatformName: 'win32' | 'darwin' | 'linux'
 *   - appOutDir:            the unpacked app directory for this target
 *   - packager.appInfo.productFilename: the exe basename (e.g. 'Hermes')
 */

const path = require('node:path')
const fs = require('node:fs')

const { stampExeIdentity } = require('./set-exe-identity.cjs')

function bundledRuntimeDir(context) {
  if (context.electronPlatformName === 'darwin') {
    const name = context.packager?.appInfo?.productFilename || 'Jolly Anrak'
    return path.join(context.appOutDir, `${name}.app`, 'Contents', 'Resources', 'bundled-runtime')
  }
  return path.join(context.appOutDir, 'resources', 'bundled-runtime')
}

function assertBundledRuntime(context) {
  const bundled = bundledRuntimeDir(context)
  const pyproject = path.join(bundled, 'pyproject.toml')
  const winInstall = path.join(bundled, 'scripts', 'install.ps1')
  const posixInstall = path.join(bundled, 'scripts', 'install.sh')
  if (!fs.existsSync(pyproject) || (!fs.existsSync(winInstall) && !fs.existsSync(posixInstall))) {
    throw new Error(
      `[after-pack] bundled-runtime is missing from ${bundled}. ` +
        'Run `npm run build` (it must stage apps/desktop/build/bundled-runtime) before packaging. ' +
        'An installer without this folder will try `git fetch` on first launch and fail.'
    )
  }
  console.log('[after-pack] bundled-runtime present at', bundled)
}

exports.default = async function afterPack(context) {
  assertBundledRuntime(context)

  if (context.electronPlatformName !== 'win32') {
    return
  }

  const productName = context.packager?.appInfo?.productFilename || 'Hermes'
  const exe = path.join(context.appOutDir, `${productName}.exe`)
  const desktopRoot = path.resolve(__dirname, '..')

  try {
    await stampExeIdentity(exe, desktopRoot)
  } catch (err) {
    // Never fail the build over a cosmetic stamp.
    console.warn(`[after-pack] exe identity stamp failed (${err.message}); Hermes.exe keeps the stock Electron icon`)
  }
}
