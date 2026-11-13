// Fork packaging overlay — Windows via NSIS/portable instead of MSIX.
//
// Kept as a separate file on purpose: `electron-builder.config.cjs` is upstream's
// only build config and is consumed verbatim by the upstream release workflow
// (.github/workflows/desktop-bundled-release.yml stages *.msix / *.msixbundle).
// Editing it in place would conflict on every upstream tag sync. A new file that
// upstream does not have can never conflict.
//
// Usage (bypassing scripts/run-electron-builder.mjs, see the note below):
//   pnpm run build
//   npx electron-builder --config electron-builder.fork.cjs --win nsis --x64
//
// @ts-check
'use strict'

const base = require('./electron-builder.config.cjs')
const { store, artifactNamePascal } = require('./product-identity.cjs')

module.exports = {
  ...base,

  // Fork builds never publish and never check for updates. Base falls back to
  // `NousResearch/hermes-agent` when GITHUB_REPOSITORY is unset
  // (electron-builder.config.cjs:60) — leaving a feed configured would offer
  // upstream releases as "updates" and silently replace this fork.
  publish: null,

  // The command-sandbox launcher ships with the app: a confined local command runs
  // through it, and `resolve_binary` falls back to this copy when `sandctl` is not on
  // PATH. `from` is relative to apps/desktop, where this config lives.
  extraResources: [
    ...base.extraResources,
    ...(process.platform === 'win32'
      ? [{ from: '../../vendor/sandctl/sandctl.exe', to: 'sandctl/sandctl.exe' }]
      : [])
  ],

  win: {
    ...base.win,
    // A store build must stay MSIX: Partner Center only accepts that identity.
    // Everything else ships as a normal Electron installer.
    target: store ? ['msix'] : ['nsis', 'portable']
  },

  nsis: {
    // NSIS and portable are one `win.target` list, and both would otherwise
    // resolve the shared `${artifactName}` and write release/<same>.exe — the
    // portable pass then silently overwrites the installer. Per-target names
    // keep both artifacts, which is what §5.5 asks for.
    artifactName: `${artifactNamePascal}-\${version}-\${os}-\${arch}-setup.\${ext}`,
    // Guided install: the user picks the installation directory (user request) —
    // the default stays per-user (%LOCALAPPDATA%\Programs\<product>), and
    // allowToChangeInstallationDirectory is what surfaces the path page. Still no
    // elevation and never per-machine: Application data lives in HERMES_HOME, not
    // here, so an overwrite install never touches memories/ or notes/ (T-12).
    oneClick: false,
    allowToChangeInstallationDirectory: true,
    allowElevation: false,
    perMachine: false
  },

  portable: {
    artifactName: `${artifactNamePascal}-\${version}-\${os}-\${arch}-portable.\${ext}`
  }
}
