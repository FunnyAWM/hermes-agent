import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  explicitSourceBackend,
  HEADLESS_RUNTIME_MISSING,
  headlessRuntime,
  headlessRuntimeRoot,
  refuseBootstrap
} from './headless-runtime'
import type { InstallStamp } from './install-stamp'
import type { SourceBackend } from './source-backend'

function stamp(payload: InstallStamp['payload'], localRuntimeRoot?: string): InstallStamp {
  return {
    schemaVersion: 1,
    commit: null,
    commitDate: null,
    branch: null,
    builtAt: null,
    dirty: false,
    source: 'local',
    distribution: 'desktop-app',
    updateMechanism: payload === 'headless' ? 'external' : 'self',
    baseVersion: null,
    displayVersion: null,
    distance: null,
    payload,
    tag: null,
    ...(localRuntimeRoot === undefined ? {} : { localRuntimeRoot })
  }
}

describe('headlessRuntime', () => {
  it('is only the headless artifact kind', () => {
    expect(headlessRuntime(null)).toBeNull()

    for (const kind of ['bootstrap', 'bundled', 'light'] as const) {
      expect(headlessRuntime(stamp(kind))).toBeNull()
    }

    expect(headlessRuntime(stamp('headless'))).toEqual({ bakedRoot: null })
  })

  it('resolves the recorded checkout so a double-click runs this machine', () => {
    const root = path.join(os.tmpdir(), 'hermes-headless-root')

    assert.equal(headlessRuntime(stamp('headless', root))?.bakedRoot, path.resolve(root))
  })

  it('treats a blank record as no record instead of resolving the cwd', () => {
    expect(headlessRuntime(stamp('headless', '   '))).toEqual({ bakedRoot: null })
  })

  it('names the way out when nothing local exists', () => {
    expect(HEADLESS_RUNTIME_MISSING).toContain('HERMES_DESKTOP_HERMES_ROOT')
    expect(HEADLESS_RUNTIME_MISSING).toContain('hermes pm install')
    expect(HEADLESS_RUNTIME_MISSING).not.toContain('bootstrap')
  })
})

const tempRoots: string[] = []

function checkout(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-headless-'))
  tempRoots.push(root)
  fs.mkdirSync(path.join(root, 'hermes_cli'))
  fs.writeFileSync(path.join(root, 'hermes_cli', 'main.py'), '')

  return root
}

function notACheckout(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-headless-'))
  tempRoots.push(root)

  return root
}

function backendAt(root: string): SourceBackend {
  return {
    kind: 'python',
    label: `Kouri Harness source at ${root}`,
    command: 'python',
    args: [],
    env: {},
    root,
    bootstrap: false,
    shell: false,
    local: 'installed'
  }
}

function rungs(backendFor: (root: string) => Promise<SourceBackend | null> = async root => backendAt(root)) {
  return { backendFor: vi.fn(backendFor), log: vi.fn() }
}

afterEach(() => {
  for (const root of tempRoots.splice(0)) {
    fs.rmSync(root, { force: true, recursive: true })
  }
})

describe('explicitSourceBackend', () => {
  it('honours the env override before the checkout a headless build recorded', async () => {
    const overrideRoot = checkout()
    const bakedRoot = checkout()
    const hooks = rungs()

    const backend = await explicitSourceBackend(
      stamp('headless', bakedRoot),
      { HERMES_DESKTOP_HERMES_ROOT: overrideRoot },
      hooks
    )

    expect(backend?.root).toBe(path.resolve(overrideRoot))
    expect(hooks.backendFor).toHaveBeenCalledTimes(1)
  })

  it('falls back to the recorded checkout when the override is not a checkout', async () => {
    const bakedRoot = checkout()
    const hooks = rungs()

    const backend = await explicitSourceBackend(
      stamp('headless', bakedRoot),
      { HERMES_DESKTOP_HERMES_ROOT: notACheckout() },
      hooks
    )

    expect(backend?.root).toBe(path.resolve(bakedRoot))
  })

  it('only drives a recorded checkout for the headless artifact kind', async () => {
    const bakedRoot = checkout()

    expect(await explicitSourceBackend(stamp('bootstrap', bakedRoot), {}, rungs())).toBeNull()
    expect(await explicitSourceBackend(null, {}, rungs())).toBeNull()
  })

  it('logs and continues down the ladder when the recorded checkout is gone', async () => {
    const hooks = rungs()

    expect(await explicitSourceBackend(stamp('headless', notACheckout()), {}, hooks)).toBeNull()
    expect(hooks.log).toHaveBeenCalledWith(expect.stringContaining('not a Kouri Harness checkout'))
  })

  it('logs and continues when the recorded checkout has no usable Python', async () => {
    const bakedRoot = checkout()
    const hooks = rungs(async () => null)

    expect(await explicitSourceBackend(stamp('headless', bakedRoot), {}, hooks)).toBeNull()
    expect(hooks.log).toHaveBeenCalledWith(expect.stringContaining('no usable Python'))
  })
})

describe('refuseBootstrap', () => {
  it('is a hard stop only for the headless artifact kind', () => {
    expect(() => refuseBootstrap(null)).not.toThrow()
    expect(() => refuseBootstrap(stamp('bootstrap'))).not.toThrow()
    expect(() => refuseBootstrap(stamp('headless'))).toThrow(HEADLESS_RUNTIME_MISSING)
  })
})

describe('headlessRuntimeRoot', () => {
  it('prefers the recorded checkout and falls back only when there is none', () => {
    expect(headlessRuntimeRoot(stamp('bundled'), () => '/fallback')).toBeNull()
    expect(headlessRuntimeRoot(stamp('headless', '/recorded/checkout'), () => '/fallback')).toBe(
      path.resolve('/recorded/checkout')
    )
    expect(headlessRuntimeRoot(stamp('headless'), () => '/fallback')).toBe('/fallback')
  })
})
