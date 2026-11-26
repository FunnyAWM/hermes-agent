// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const confirmMock = vi.fn()

vi.mock('@/store/confirm', () => ({ confirm: (...args: unknown[]) => confirmMock(...args) }))
vi.mock('@/store/notifications', () => ({ notifyError: vi.fn() }))

const choices = (names: string[]) => names.map(name => ({ name, preset: name }))

const sandboxState = (
  active: string,
  onPick = vi.fn().mockResolvedValue(undefined),
  names?: string[],
  over: { approvalMode?: 'auto' | 'smart'; onToggleAutoReview?: (enabled: boolean) => Promise<void> } = {}
) => ({
  active,
  approvalMode: over.approvalMode ?? 'smart',
  profiles: choices(names ?? ['read-only', 'workspace-write', 'workspace-exec']),
  onPick,
  onToggleAutoReview: over.onToggleAutoReview ?? vi.fn().mockResolvedValue(undefined)
})

describe('SandboxPill', () => {
  beforeEach(() => {
    confirmMock.mockReset().mockResolvedValue(true)
  })

  afterEach(cleanup)

  it('renders nothing without sandbox state (off or still loading)', async () => {
    const { SandboxPill } = await import('./sandbox-pill')

    const { container } = render(<SandboxPill disabled={false} />)

    expect(container.innerHTML).toBe('')
  })

  it('shows the active profile and hands a pick to the state owner', async () => {
    const { SandboxPill } = await import('./sandbox-pill')
    const onPick = vi.fn().mockResolvedValue(undefined)
    render(<SandboxPill disabled={false} sandbox={sandboxState('workspace-write', onPick)} />)

    fireEvent.keyDown(screen.getByRole('button', { name: /Sandbox profile: workspace-write/ }), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('menuitemradio', { name: /read-only/ }))

    await waitFor(() => expect(onPick).toHaveBeenCalledWith('read-only'))
  })

  it('toggles Auto Review without moving the sandbox profile', async () => {
    const { SandboxPill } = await import('./sandbox-pill')
    const onPick = vi.fn().mockResolvedValue(undefined)
    const onToggleAutoReview = vi.fn().mockResolvedValue(undefined)
    render(
      <SandboxPill
        disabled={false}
        sandbox={sandboxState('workspace-write', onPick, undefined, { onToggleAutoReview })}
      />
    )

    fireEvent.keyDown(screen.getByRole('button', { name: /Sandbox profile: workspace-write/ }), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('menuitemcheckbox', { name: /Auto Review/ }))

    await waitFor(() => expect(onToggleAutoReview).toHaveBeenCalledWith(true))
    expect(onPick).not.toHaveBeenCalled()
  })

  it('shows Auto Review as checked while the approval mode is auto', async () => {
    const { SandboxPill } = await import('./sandbox-pill')
    render(
      <SandboxPill
        disabled={false}
        sandbox={sandboxState('workspace-write', undefined, undefined, { approvalMode: 'auto' })}
      />
    )

    fireEvent.keyDown(screen.getByRole('button', { name: /Sandbox profile/ }), { key: 'Enter' })

    expect((await screen.findByRole('menuitemcheckbox', { name: /Auto Review/ })).getAttribute('aria-checked')).toBe(
      'true'
    )
  })

  it('asks for consent before selecting full access', async () => {
    const { SandboxPill } = await import('./sandbox-pill')
    const onPick = vi.fn().mockResolvedValue(undefined)
    render(
      <SandboxPill
        disabled={false}
        sandbox={sandboxState('workspace-write', onPick, ['workspace-write', 'full-access'])}
      />
    )

    fireEvent.keyDown(screen.getByRole('button', { name: /Sandbox profile/ }), { key: 'Enter' })
    fireEvent.click(await screen.findByRole('menuitemradio', { name: /full-access/ }))

    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(onPick).toHaveBeenCalledWith('full-access'))
  })
})
