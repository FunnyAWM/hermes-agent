// headless-runtime.ts — the local-runtime contract of the 'headless' artifact.
//
// A headless build carries no agent runtime and never installs one: it drives
// whatever Kouri Harness this machine already has. The builder records the checkout it
// was made against, so a double-click works on that machine; the explicit
// HERMES_DESKTOP_HERMES_ROOT override still wins, and every installed-runtime
// rung of resolveHermesBackend() still applies when the record is stale.
import path from 'node:path'

import type { InstallStamp } from './install-stamp'
import { isHermesSourceRoot, type SourceBackend } from './source-backend'

export interface HeadlessRuntime {
  /** Absolute root recorded at build time, when the stamp carries one. */
  bakedRoot: string | null
}

export function headlessRuntime(stamp: Readonly<InstallStamp> | null): HeadlessRuntime | null {
  if (stamp?.payload !== 'headless') {
    return null
  }

  const recorded = stamp.localRuntimeRoot?.trim()

  return { bakedRoot: recorded ? path.resolve(recorded) : null }
}

/** Shown when a headless artifact finds nothing to drive; it must not bootstrap. */
export const HEADLESS_RUNTIME_MISSING =
  'This headless build found no local Kouri Harness runtime, and it never installs one. ' +
  'Point HERMES_DESKTOP_HERMES_ROOT at a checkout, or install Kouri Harness with `hermes pm install`.'

export interface ExplicitSourceHooks {
  backendFor: (root: string) => Promise<SourceBackend | null>
  log: (message: string) => void
}

/** Rungs 1-1b: the developer override, then the checkout a headless build drove. */
export async function explicitSourceBackend(
  stamp: Readonly<InstallStamp> | null,
  env: NodeJS.ProcessEnv,
  hooks: ExplicitSourceHooks
): Promise<SourceBackend | null> {
  const overrideRoot: string | undefined =
    env.HERMES_DESKTOP_HERMES_ROOT && path.resolve(env.HERMES_DESKTOP_HERMES_ROOT)

  if (overrideRoot && isHermesSourceRoot(overrideRoot)) {
    const backend: SourceBackend | null = await hooks.backendFor(overrideRoot)

    if (backend) {
      return backend
    }
  }

  const bakedRoot: string | null = headlessRuntime(stamp)?.bakedRoot ?? null

  if (!bakedRoot) {
    return null
  }

  if (isHermesSourceRoot(bakedRoot)) {
    const backend: SourceBackend | null = await hooks.backendFor(bakedRoot)

    if (backend) {
      return backend
    }

    hooks.log(`[boot] headless runtime root ${bakedRoot} has no usable Python; continuing down the ladder`)
  } else {
    hooks.log(`[boot] headless runtime root is not a Kouri Harness checkout: ${bakedRoot}; continuing down the ladder`)
  }

  return null
}

/** A headless artifact drives this machine's runtime; it must never bootstrap one. */
export function refuseBootstrap(stamp: Readonly<InstallStamp> | null): void {
  if (stamp?.payload === 'headless') {
    throw new Error(HEADLESS_RUNTIME_MISSING)
  }
}

/** The runtime root to report for a headless artifact, or null for other kinds. */
export function headlessRuntimeRoot(stamp: Readonly<InstallStamp> | null, fallback: () => string): string | null {
  if (stamp?.payload !== 'headless') {
    return null
  }

  return headlessRuntime(stamp)?.bakedRoot ?? fallback()
}
