// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { SandboxProfilesPanel } from './sandbox-profiles-panel'

const baseConfig = {
  terminal: {
    sandbox: {
      enabled: true,
      profile: 'workspace-write',
      profiles: {
        'read-only': { preset: 'read-only' },
        'workspace-write': { preset: 'workspace-write' },
        'workspace-exec': { preset: 'workspace-exec' }
      }
    }
  }
}

const sandboxOf = (config: Record<string, unknown>) =>
  (config.terminal as { sandbox: Record<string, unknown> }).sandbox

describe('SandboxProfilesPanel', () => {
  afterEach(cleanup)

  it('lists the shipped presets with their boundary descriptions', () => {
    render(<SandboxProfilesPanel activeSectionId="workspace" config={baseConfig} onChange={() => {}} subpage="shell" />)

    for (const name of ['read-only', 'workspace-write', 'workspace-exec']) {
      expect(screen.getAllByText(name).length).toBeGreaterThan(0)
    }

    expect(screen.getAllByText(/network stays closed/i).length).toBeGreaterThan(0)
    expect(screen.queryByText('Write directories')).toBeNull()
  })

  it('shows the shipped presets for a config without the sandbox section', () => {
    render(<SandboxProfilesPanel activeSectionId="workspace" config={{}} onChange={() => {}} subpage="shell" />)

    expect(screen.getAllByText('Read only').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Workspace Exec').length).toBeGreaterThan(0)
  })

  it('adds a named profile without touching the active one', () => {
    const onChange = vi.fn()
    render(<SandboxProfilesPanel activeSectionId="workspace" config={baseConfig} onChange={onChange} subpage="shell" />)

    fireEvent.change(screen.getByPlaceholderText('Profile name'), { target: { value: 'rust-build' } })
    fireEvent.click(screen.getByRole('button', { name: /Add profile/ }))

    const sandbox = sandboxOf(onChange.mock.calls[0][0])
    expect(Object.keys(sandbox.profiles as object)).toContain('rust-build')
    expect(sandbox.profile).toBe('workspace-write')
  })

  it('switches the active profile when the active one is deleted', () => {
    const onChange = vi.fn()
    render(<SandboxProfilesPanel activeSectionId="workspace" config={baseConfig} onChange={onChange} subpage="shell" />)

    fireEvent.click(screen.getByRole('button', { name: 'Delete profile workspace-write' }))

    const sandbox = sandboxOf(onChange.mock.calls[0][0])
    expect(Object.keys(sandbox.profiles as object)).not.toContain('workspace-write')
    expect(sandbox.profile).toBe('read-only')
  })

  it('shows path editors only for a custom profile', () => {
    const custom = {
      terminal: {
        sandbox: {
          profile: 'mine',
          profiles: { mine: { preset: 'custom', read: ['C:\\shared'], write: [] } }
        }
      }
    }

    render(<SandboxProfilesPanel activeSectionId="workspace" config={custom} onChange={() => {}} subpage="shell" />)

    expect(screen.getByText('Read directories')).toBeTruthy()
    expect(screen.getByText('Write directories')).toBeTruthy()
    expect((screen.getByDisplayValue('C:\\shared') as HTMLTextAreaElement).value).toBe('C:\\shared')
  })

  it('keeps typed path lines verbatim while the config round-trips', () => {
    const custom = {
      terminal: {
        sandbox: {
          profile: 'mine',
          profiles: { mine: { preset: 'custom', read: ['C:\\shared'], write: [] } }
        }
      }
    }

    const onChange = vi.fn()

    function StatefulPanel() {
      const [config, setConfig] = useState<Record<string, unknown>>(custom)

      return (
        <SandboxProfilesPanel
          activeSectionId="workspace"
          config={config}
          onChange={next => {
            setConfig(next)
            onChange(next)
          }}
          subpage="shell"
        />
      )
    }

    render(<StatefulPanel />)

    const read = screen.getByDisplayValue('C:\\shared') as HTMLTextAreaElement
    // The newline that starts the next path must survive the round-trip re-render.
    fireEvent.change(read, { target: { value: 'C:\\shared\n' } })
    expect(read.value).toBe('C:\\shared\n')

    // Lines keep their leading spaces; only blank lines drop out of the stored list.
    fireEvent.change(read, { target: { value: 'C:\\shared\n\n  C:\\spaced' } })
    expect(read.value).toBe('C:\\shared\n\n  C:\\spaced')
    const sandbox = sandboxOf(onChange.mock.calls.at(-1)?.[0])
    expect((sandbox.profiles as Record<string, { read?: string[] }>).mine.read).toEqual(['C:\\shared', '  C:\\spaced'])
  })

  it('renders the panel copy in the selected locale', () => {
    render(
      <I18nProvider configClient={null} initialLocale="zh">
        <SandboxProfilesPanel activeSectionId="workspace" config={baseConfig} onChange={() => {}} subpage="shell" />
      </I18nProvider>
    )

    expect(screen.getByText('命令沙箱配置')).toBeTruthy()
    expect(screen.getAllByText('工作区可执行').length).toBeGreaterThan(0)
    expect(screen.getByPlaceholderText('配置名称')).toBeTruthy()
    expect(screen.getByRole('button', { name: /添加配置/ })).toBeTruthy()
    expect(screen.getByText('当前配置')).toBeTruthy()
  })
})

describe('SandboxProfilesPanel custom policy overrides', () => {
  afterEach(cleanup)

  const custom = {
    terminal: {
      sandbox: {
        profile: 'mine',
        profiles: {
          mine: {
            preset: 'custom',
            backend: 'l2',
            policy: {
              net: { default: 'allow', allowCidr: ['10.0.0.0/8:443'] },
              syscall: { profile: 'strict' },
              proc: { cpu: '2' }
            }
          }
        }
      }
    }
  }

  it('shows the sandctl knobs a custom profile carries', () => {
    render(<SandboxProfilesPanel activeSectionId="workspace" config={custom} onChange={() => {}} subpage="shell" />)

    expect(screen.getByText('sandctl policy overrides')).toBeTruthy()
    expect(screen.getByText('Isolation level')).toBeTruthy()
    expect(screen.getByText('Syscall filter')).toBeTruthy()
    expect(screen.getByText('Network mode')).toBeTruthy()
    expect(screen.getByText('Network default')).toBeTruthy()
    expect(screen.getByText('Allowed CIDRs')).toBeTruthy()
    expect(screen.getByDisplayValue('10.0.0.0/8:443')).toBeTruthy()
    expect(screen.getByDisplayValue('2')).toBeTruthy()
  })

  it('persists an edited cap and drops it when cleared', () => {
    const onChange = vi.fn()
    render(<SandboxProfilesPanel activeSectionId="workspace" config={custom} onChange={onChange} subpage="shell" />)

    fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '4' } })

    let policy = (sandboxOf(onChange.mock.calls[0][0]).profiles as Record<string, { policy?: { proc?: { cpu?: string } } }>)
      .mine.policy

    expect(policy?.proc?.cpu).toBe('4')

    fireEvent.change(screen.getByDisplayValue('2'), { target: { value: '' } })
    policy = (sandboxOf(onChange.mock.calls[1][0]).profiles as Record<string, { policy?: { proc?: { cpu?: string } } }>)
      .mine.policy
    expect(policy?.proc?.cpu).toBeUndefined()
  })
})
