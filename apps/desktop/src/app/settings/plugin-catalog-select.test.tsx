import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { PluginSettingsForm } from '@/app/settings/plugin-settings-form'
import { localizeAgentPlugin } from '@/i18n/agent-plugin-settings'
import { I18nProvider } from '@/i18n/context'
import type { AgentPluginRow } from '@/store/agent-plugins'

const catalog = vi.hoisted(() => vi.fn())
vi.mock('@/hermes', async () => ({ ...(await vi.importActual('@/api/client')), getGlobalModelOptions: catalog }))
afterEach(cleanup)

it('saves Chinese worker dropdown selections from the owning profile and clears the old model', async () => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
  catalog.mockResolvedValue({
    provider: 'one',
    providers: [
      { slug: 'one', name: 'First', models: ['first-model'] },
      { slug: 'custom:two', name: 'Second', models: ['second-model'] }
    ]
  })

  const row: AgentPluginRow = {
    key: 'super_agent',
    name: 'Super Agent',
    version: '',
    description: '',
    source: 'bundled',
    status: 'enabled',
    settings_schema: ['worker_provider', 'worker_model'].map((key, index) => ({
      key,
      model_provider_key: index ? 'worker_provider' : undefined,
      type: 'string',
      label: key,
      description: '',
      required: false,
      value: index ? 'legacy-model' : 'one'
    }))
  }

  const localized = localizeAgentPlugin(row, 'zh')
  const save = vi.fn(async () => true)
  render(
    <I18nProvider configClient={null} initialLocale="zh">
      <QueryClientProvider client={new QueryClient()}>
        <PluginSettingsForm
          disabled={false}
          fields={localized.settings_schema!}
          idPrefix="super"
          onSave={save}
          scope="research"
          title={localized.name}
        />
      </QueryClientProvider>
    </I18nProvider>
  )
  await waitFor(() => expect(catalog).toHaveBeenCalledWith(undefined, 'research'))
  const provider = screen.getByRole('combobox', { name: '工作代理提供商' })
  const model = screen.getByRole('combobox', { name: '工作代理模型' })
  expect(model.textContent).toBe('legacy-model')
  fireEvent.click(provider)
  fireEvent.click(await screen.findByRole('option', { name: 'Second' }))
  expect(model.textContent).not.toBe('legacy-model')
  fireEvent.click(model)
  expect(screen.queryByRole('option', { name: 'first-model' })).toBeNull()
  fireEvent.click(await screen.findByRole('option', { name: 'second-model' }))
  fireEvent.submit(screen.getByTestId('super-settings-form'))
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith({
      values: {
        worker_provider: 'custom:two',
        worker_model: 'second-model'
      },
      secrets: {}
    })
  )
})
