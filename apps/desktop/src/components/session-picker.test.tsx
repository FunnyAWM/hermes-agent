// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '@/i18n/en'
import { $removedSessionIds } from '@/store/session-removal'
import type { SessionInfo } from '@/types/hermes'

import { SessionPickerDialog } from './session-picker'

const listAllProfileSessions = vi.hoisted(() => vi.fn())

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<Record<string, unknown>>()),
  getApiRequestConnection: () => 'local',
  listAllProfileSessions
}))

vi.mock('@/i18n', () => ({ useI18n: () => ({ t: en }) }))

const row = (overrides: Partial<SessionInfo>): SessionInfo =>
  ({
    archived: false,
    id: 'row',
    message_count: 1,
    preview: null,
    source: 'desktop',
    started_at: 1,
    title: 'row',
    ...overrides
  }) as SessionInfo

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

beforeEach(() => {
  listAllProfileSessions.mockReset()
  $removedSessionIds.set(new Set())
})

afterEach(() => {
  cleanup()
  $removedSessionIds.set(new Set())
})

describe('SessionPickerDialog removal', () => {
  it('hides a just-deleted row the cached page still lists, matched by lineage id', async () => {
    listAllProfileSessions.mockResolvedValue({
      sessions: [
        row({ id: 'keep', title: 'live chat' }),
        row({ _lineage_root_id: 'gone-root', id: 'gone-tip', title: 'deleted chat' })
      ]
    })
    $removedSessionIds.set(new Set(['gone-root']))

    render(<SessionPickerDialog onOpenChange={() => {}} onResume={() => {}} open />, { wrapper })

    expect(await screen.findByText('live chat')).toBeTruthy()
    expect(screen.queryByText('deleted chat')).toBeNull()
  })
})
