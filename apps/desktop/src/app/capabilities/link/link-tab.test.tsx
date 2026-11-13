import { QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { linkErrorOf } from '@/api/kourichat-link'
import { I18nProvider } from '@/i18n'
import { queryClient } from '@/lib/query-client'

import { LinkTab } from './link-tab'

afterEach(() => {
  cleanup()
  queryClient.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function renderTab() {
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <LinkTab profile="default" />
      </I18nProvider>
    </QueryClientProvider>
  )
}

const NOT_PAIRED = {
  ok: true,
  role: 'disabled',
  paired: false,
  device_id: null,
  master_endpoint: '',
  listen_port: 27890,
  auto_sync_minutes: 0,
  pinned_fingerprint: null,
  last_sync_at_ms: null,
  last_sync_error: null,
  applied_count: 0,
  archived_count: 0,
  tracked_files: 0,
  syncable_files: 0,
  serving: false
}

const PAIRED_SECONDARY = {
  ...NOT_PAIRED,
  role: 'secondary',
  paired: true,
  device_id: 'a'.repeat(32),
  master_endpoint: '192.168.1.20:27890',
  pinned_fingerprint: 'b'.repeat(64),
  tracked_files: 4,
  syncable_files: 4
}

describe('LinkTab', () => {
  it('offers pairing and chain creation when not paired', async () => {
    vi.stubGlobal('hermesDesktop', {
      api: vi.fn().mockResolvedValue(NOT_PAIRED)
    })
    renderTab()
    expect(await screen.findByText('Not paired to a KouriChat Link chain')).toBeTruthy()
    expect(screen.getByText('Pair with master')).toBeTruthy()
    expect(screen.getByText('Create a chain')).toBeTruthy()
  })

  it('shows the master and the sync action when paired as secondary', async () => {
    vi.stubGlobal('hermesDesktop', {
      api: vi.fn().mockResolvedValue(PAIRED_SECONDARY)
    })
    renderTab()
    expect(await screen.findByText('KouriChat Link')).toBeTruthy()
    expect(screen.getByText('Secondary — pulling from the master')).toBeTruthy()
    expect(screen.getByText('Sync now')).toBeTruthy()
    expect(screen.getByText('Leave chain')).toBeTruthy()
  })

  it('normalizes the FastAPI error envelope and the plain-string fallback', () => {
    const envelope = new Error('{"detail": {"code": "HM-6001", "message": "unreachable"}}')
    expect(linkErrorOf(envelope)).toEqual({ code: 'HM-6001', message: 'unreachable' })
    const plain = new Error('backend exploded')
    expect(linkErrorOf(plain)).toEqual({ code: 'UNKNOWN', message: 'backend exploded' })
  })
})
