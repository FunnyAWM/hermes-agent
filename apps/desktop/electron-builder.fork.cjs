// Fork packaging overlay — Windows via NSIS/portable instead of MSIX.
//
// Kept as a separate file on purpose: `electron-builder.config.cjs` is upstream's
// only build config and is consumed verbatim by the upstream release workflow
// (.github/workflows/desktop-bundled-release.yml stages *.msix / *.msixbundle).
// Editing it in place would conflict on every upstream tag sync. A new file that
// upstream does not have can never conflict.
//
// Usage (bypassing scripts/run-electron-builder.mjs, see the note below):
//   npm run build
//   npx electron-builder --config electron-builder.fork.cjs --win nsis --x64
//
// @ts-check
'use strict'

const base = require('./electron-builder.config.cjs')
const { store } = require('./product-identity.cjs')

module.exports = {
  ...base,

  // Fork builds never publish and never check for updates. Base falls back to
  // `NousResearch/hermes-agent` when GITHUB_REPOSITORY is unset
  // (electron-builder.config.cjs:60) — leaving a feed configured would offer
  // upstream releases as "updates" and silently replace this fork.
  publish: null,

  win: {
    ...base.win,
    // A store build must stay MSIX: Partner Center only accepts that identity.
    // Everything else ships as a normal Electron installer.
    target: store ? ['msix'] : ['nsis', 'portable']
  },

  nsis: {
    // Per-user install: no administrator prompt, and the install tree stays out
    // of Program Files. Application data lives in HERMES_HOME, not here, so an
    // overwrite install never touches memories/ or notes/ (T-12's criterion).
    oneClick: true,
    perMachine: false
  }
}
