import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { $apiRequestScope } from '@/api/client'
import { I18nProvider } from '@/i18n/context'
import type { PluginSettingField } from '@/store/agent-plugins'

import { PluginSettingsForm } from './plugin-settings-form'

const platforms = vi.hoisted(() => vi.fn())
vi.mock('@/api/messaging', () => ({ getMessagingPlatforms: platforms }))

const originalScope = $apiRequestScope.get()
afterEach(() => {
  cleanup()
  $apiRequestScope.set(originalScope)
  platforms.mockReset()
})

const fields: PluginSettingField[] = [
  { key: 'message_sources', label: 'Read', value: ['retired'], type: 'json', required: false, description: '' },
  {
    key: 'write_message_sources',
    label: 'Write',
    value: ['same-as-read'],
    type: 'json',
    required: false,
    description: ''
  }
]

it('saves scoped IM selections, read/write modes and empty lists without losing a retired selection', async () => {
  platforms.mockImplementation(async ({ connectionId }) => ({
    platforms: [
      { id: connectionId, name: connectionId, configured: true, enabled: false, home_channel: null },
      { id: 'slack', name: 'Slack', configured: false },
      { id: 'webhook', name: 'Webhook', configured: true }
    ]
  }))
  $apiRequestScope.set({ connectionId: 'first', profile: 'ambient' })
  const save = vi.fn(async () => true)
  render(
    <I18nProvider configClient={null} initialLocale="zh">
      <QueryClientProvider client={new QueryClient()}>
        <PluginSettingsForm
          bundledPlugin="super_agent"
          disabled={false}
          fields={fields}
          idPrefix="super"
          onSave={save}
          scope="research"
          title="Super Agent"
        />
      </QueryClientProvider>
    </I18nProvider>
  )
  const open = (name: string) => fireEvent.keyDown(screen.getByRole('button', { name }), { key: 'Enter' })
  const close = () => fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
  const choice = (name: string) => screen.getByRole('menuitemcheckbox', { name })
  open('Read')
  await screen.findByRole('menuitemcheckbox', { name: 'first' })
  expect(platforms).toHaveBeenCalledWith({ connectionId: 'first', profile: 'research' })
  expect(choice('retired').getAttribute('aria-checked')).toBe('true')
  expect(screen.queryByText('Slack')).toBeNull()
  expect(screen.queryByText('Webhook')).toBeNull()
  expect(screen.queryByRole('menuitemcheckbox', { name: '沿用读取来源' })).toBeNull()

  act(() => $apiRequestScope.setKey('connectionId', 'second'))
  fireEvent.click(await screen.findByRole('menuitemcheckbox', { name: 'second' }))
  expect(screen.queryByRole('menuitemcheckbox', { name: 'first' })).toBeNull()
  expect(choice('retired').getAttribute('aria-checked')).toBe('true')
  fireEvent.click(choice('内置机器人'))
  fireEvent.click(choice('retired'))
  close()
  open('Write')
  expect(choice('沿用读取来源').getAttribute('aria-checked')).toBe('true')
  fireEvent.click(choice('内置机器人'))
  expect(choice('沿用读取来源').getAttribute('aria-checked')).toBe('false')
  fireEvent.click(choice('全部来源'))
  expect(choice('内置机器人').getAttribute('aria-checked')).toBe('false')
  close()
  fireEvent.submit(screen.getByTestId('super-settings-form'))
  await waitFor(() =>
    expect(save).toHaveBeenLastCalledWith({
      values: { message_sources: ['second', 'local'], write_message_sources: ['*'] },
      secrets: {}
    })
  )
  open('Write')
  fireEvent.click(choice('无'))
  close()
  fireEvent.submit(screen.getByTestId('super-settings-form'))
  await waitFor(() =>
    expect(save).toHaveBeenLastCalledWith({
      values: { message_sources: ['second', 'local'], write_message_sources: [] },
      secrets: {}
    })
  )
})
