import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { MemoryView } from '.'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const DOCS = {
  ok: true,
  home: 'C:/hermes',
  files: [
    { id: 'soul', path: 'SOUL.md', exists: true, content: '# Soul\nbe kind', bytes: 14, too_large: false, editable: true },
    { id: 'agents', path: 'AGENTS.md', exists: false, content: '', bytes: 0, too_large: false, editable: true },
    { id: 'user', path: 'memories/USER.md', exists: true, content: 'likes tea', bytes: 9, too_large: false, editable: true },
    { id: 'memory', path: 'memories/MEMORY.md', exists: true, content: 'remembers', bytes: 9, too_large: false, editable: true }
  ],
  notes: [
    { path: 'notes/work/2019-trip.md', bytes: 5, updated_at_ms: 1, editable: true },
    { path: 'notes/work/deep/brief.md', bytes: 5, updated_at_ms: 1, editable: true },
    { path: 'notes/docs/import.md', bytes: 5, updated_at_ms: 1, editable: false }
  ]
}

const NOTE_BODY = { ok: true, path: 'notes/work/2019-trip.md', content: 'note body', bytes: 9, too_large: false }

function stubApi() {
  const api = vi.fn(async (request: { method?: string; path: string; body?: { path: string; content: string } }) => {
    if (request.path.endsWith('/memory/docs')) {
      return DOCS
    }

    if (request.method === 'POST') {
      return { ok: true, path: request.body?.path ?? '', bytes: 1, updated_at_ms: 0 }
    }

    return { ...NOTE_BODY, path: decodeURIComponent(request.path.split('path=')[1] ?? '') }
  })

  vi.stubGlobal('hermesDesktop', { api })

  return api
}

function renderView() {
  return render(
    <I18nProvider>
      <MemoryView />
    </I18nProvider>
  )
}

describe('MemoryView', () => {
  it('lists the four context files and groups notes into folders', async () => {
    stubApi()

    renderView()

    await waitFor(() => expect(screen.getAllByText('SOUL.md').length).toBeGreaterThan(0))
    expect(screen.getAllByText('AGENTS.md').length).toBeGreaterThan(0)
    expect(screen.getAllByText('memories/USER.md').length).toBeGreaterThan(0)
    expect(screen.getAllByText('memories/MEMORY.md').length).toBeGreaterThan(0)
    // `notes/work/2019-trip.md` is one folder deep, `notes/work/deep/brief.md` two:
    // the tree shows folder rows, not the whole path on every row.
    expect(screen.getByText('work')).toBeTruthy()
    expect(screen.getByText('deep')).toBeTruthy()
    expect(screen.getByText('2019-trip')).toBeTruthy()
    expect(screen.getByText('brief')).toBeTruthy()
    // The default selection is SOUL.md, opened as an editable draft.
    expect((screen.getByLabelText('SOUL.md') as HTMLTextAreaElement).value).toBe('# Soul\nbe kind')
  })

  it('collapses a folder without losing the selection', async () => {
    stubApi()

    renderView()
    await waitFor(() => expect(screen.getByText('work')).toBeTruthy())
    screen.getByText('work').click()

    await waitFor(() => expect(screen.queryByText('2019-trip')).toBeNull())
    expect(screen.getByText('work')).toBeTruthy()
  })

  it('saves an edited context file', async () => {
    const api = stubApi()

    renderView()
    const area = (await waitFor(() => screen.getByLabelText('SOUL.md'))) as HTMLTextAreaElement
    fireEvent.change(area, { target: { value: '# Soul\nbe kind, always' } })

    await waitFor(() => expect(screen.getByText('Unsaved changes')).toBeTruthy())
    screen.getByText('Save').click()

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        expect.objectContaining({ body: { path: 'SOUL.md', content: '# Soul\nbe kind, always' }, method: 'POST' })
      )
    )
    await waitFor(() => expect(screen.getByText('Saved')).toBeTruthy())
  })

  it('refuses to edit an imported document under notes/docs/', async () => {
    stubApi()

    renderView()
    await waitFor(() => expect(screen.getByText('import')).toBeTruthy())
    screen.getByText('import').click()

    await waitFor(() => expect(screen.getByText('Read-only import')).toBeTruthy())
    expect(screen.queryByLabelText('notes/docs/import.md')).toBeNull()
  })

  it('says a missing file was never created instead of showing an empty box', async () => {
    stubApi()

    renderView()
    await waitFor(() => expect(screen.getAllByText('AGENTS.md').length).toBeGreaterThan(0))
    screen.getAllByText('AGENTS.md')[0].click()

    await waitFor(() => expect(screen.getByText('Not created yet — saving will create it.')).toBeTruthy())
  })

  it('surfaces a read failure with a retry action', async () => {
    vi.stubGlobal('hermesDesktop', { api: vi.fn().mockRejectedValue(new Error('boom')) })

    renderView()

    await waitFor(() => expect(screen.getByText('Could not read the memory files.')).toBeTruthy())
    expect(screen.getByText('Retry')).toBeTruthy()
  })
})
