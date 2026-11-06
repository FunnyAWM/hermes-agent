/**
 * The Bots pane with only its gateway transport mocked.
 *
 * Fork rule (PRD §5.5 单机器人): the pane drives one assistant, so the gateway
 * Group Chats `groups.list` returns are never offered here — a failing bot list
 * is not masked by a room list this pane cannot act on.
 */

import type * as HermesSdk from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, expect, it, vi } from 'vitest'

import { translateBotsIn } from './i18n-test-helper'

const { request, requestProfile } = vi.hoisted(() => ({ request: vi.fn(), requestProfile: vi.fn() }))

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()

  return {
    ...sdk,
    host: {
      ...sdk.host,
      agents: vi.fn(async () => { throw new Error('profiles.list exploded') }),
      request,
      requestProfile,
      state: { ...sdk.host.state, connectionId: atom<null | string>('local'), gateway: atom('open'), profile: atom('default') }
    },
    usePluginI18n: () => translateBotsIn('en')
  }
})

import { BotsPane } from './roster-pane'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('keeps gateway Group Chats out of the single-bot pane', async () => {
  request.mockRejectedValue(new Error('profiles.list exploded'))
  requestProfile.mockImplementation(async (_route: unknown, method: string) => {
    if (method === 'groups.list') {
      return { rooms: [{ room_id: 'room-1', name: 'Launch crew', members: [], updated_at: Date.now() / 1000 }], next_offset: null }
    }

    throw new Error(`unexpected ${method}`)
  })

  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <BotsPane />
    </QueryClientProvider>
  )

  expect(await screen.findByText(/Roster unavailable/)).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Launch crew' })).toBeNull()
})
