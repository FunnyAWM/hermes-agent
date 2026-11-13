// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type * as ConfigApi from '@/api/config'
import { $approvalModes } from '@/store/approval-mode'

import { useChatBarState } from './use-chat-bar-state'

// Hoisted so the mock factory can close over it while the static import runs.
const { saveHermesConfig, writeCache, requestGatewayForActiveRoute } = vi.hoisted(() => ({
  saveHermesConfig: vi.fn(),
  writeCache: vi.fn(),
  requestGatewayForActiveRoute: vi.fn()
}))

vi.mock('@/store/active-route-request', () => ({ requestGatewayForActiveRoute }))
let readProfile: string | undefined
let writerProfile: string | undefined
let config: Record<string, unknown>

vi.mock('@/api/config', async importOriginal => ({
  ...(await importOriginal<typeof ConfigApi>()),
  saveHermesConfig
}))

vi.mock('../hooks/use-config-record', () => ({
  useHermesConfigRecord: (profile?: string) => {
    readProfile = profile

    return { data: config }
  },
  hermesConfigCacheWriter: (profile?: string) => {
    writerProfile = profile

    return writeCache
  }
}))

const sandboxConfig = {
  terminal: {
    sandbox: {
      enabled: true,
      profile: 'workspace-write',
      profiles: {
        'read-only': { preset: 'read-only' },
        'workspace-write': { preset: 'workspace-write' }
      }
    }
  }
}

const baseOptions = {
  activeGatewayProfile: 'default',
  activeSessionId: 'session-1',
  contextSuggestions: [],
  currentModel: 'gpt-6',
  currentProvider: 'openai',
  gateway: null,
  gatewayOpen: false
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('useChatBarState sandbox scope', () => {
  beforeEach(() => {
    saveHermesConfig.mockReset().mockResolvedValue({ ok: true })
    writeCache.mockReset()
    $approvalModes.set({})
    requestGatewayForActiveRoute.mockReset().mockImplementation(async (_profile, _method, params) => ({
      value: params?.value
    }))
    config = sandboxConfig
    readProfile = undefined
    writerProfile = undefined
  })

  it('reads and writes the app-wide config row when the tile has no profile pin', async () => {
    const { result } = renderHook(() => useChatBarState(baseOptions), { wrapper })

    expect(readProfile).toBeUndefined()
    expect(result.current.sandbox?.active).toBe('workspace-write')

    await act(async () => {
      await result.current.sandbox?.onPick('read-only')
    })

    expect(saveHermesConfig).toHaveBeenCalledWith({ terminal: { sandbox: { profile: 'read-only' } } }, undefined)
    // The cache write must land on the same row the read used, or the pill keeps
    // painting the pre-switch profile.
    expect(writerProfile).toBe(readProfile)
    expect(writeCache).toHaveBeenCalled()
  })

  it('keeps the ambient profile on the shared row when the tile passes the active profile', async () => {
    const { result } = renderHook(() => useChatBarState({ ...baseOptions, modelOptionsProfile: 'default' }), {
      wrapper
    })

    // Session tiles pass `activeGatewayProfile` verbatim; an undefined scope is what
    // every other config surface reads, so the pill sees their saves too.
    expect(readProfile).toBeUndefined()

    await act(async () => {
      await result.current.sandbox?.onPick('read-only')
    })

    expect(saveHermesConfig).toHaveBeenCalledWith({ terminal: { sandbox: { profile: 'read-only' } } }, undefined)
    expect(writerProfile).toBeUndefined()
  })

  it('turns Auto Review on and restores the mode it replaced', async () => {
    $approvalModes.set({ default: 'manual' })
    const { result } = renderHook(() => useChatBarState(baseOptions), { wrapper })

    expect(result.current.sandbox?.approvalMode).toBe('manual')

    await act(async () => {
      await result.current.sandbox?.onToggleAutoReview(true)
    })

    expect(requestGatewayForActiveRoute).toHaveBeenCalledWith('default', 'config.set', {
      key: 'approvals.mode',
      value: 'auto',
      profile: 'default'
    })
    expect(result.current.sandbox?.approvalMode).toBe('auto')

    await act(async () => {
      await result.current.sandbox?.onToggleAutoReview(false)
    })

    expect(requestGatewayForActiveRoute).toHaveBeenLastCalledWith('default', 'config.set', {
      key: 'approvals.mode',
      value: 'manual',
      profile: 'default'
    })
    expect(result.current.sandbox?.approvalMode).toBe('manual')
  })

  it("scopes Auto Review to the tile's own profile", async () => {
    const { result } = renderHook(() => useChatBarState({ ...baseOptions, modelOptionsProfile: 'coder' }), { wrapper })

    await act(async () => {
      await result.current.sandbox?.onToggleAutoReview(true)
    })

    expect(requestGatewayForActiveRoute).toHaveBeenCalledWith('coder', 'config.set', {
      key: 'approvals.mode',
      value: 'auto',
      profile: 'coder'
    })
  })

  it('pins a tile with its own profile to that profile for both the read and the write', async () => {
    const { result } = renderHook(() => useChatBarState({ ...baseOptions, modelOptionsProfile: 'coder' }), { wrapper })

    expect(readProfile).toBe('coder')
    expect(result.current.sandbox?.active).toBe('workspace-write')

    await act(async () => {
      await result.current.sandbox?.onPick('read-only')
    })

    expect(saveHermesConfig).toHaveBeenCalledWith({ terminal: { sandbox: { profile: 'read-only' } } }, 'coder')
    expect(writerProfile).toBe('coder')
  })
})
