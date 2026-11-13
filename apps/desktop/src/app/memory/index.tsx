import { useCallback, useEffect, useState } from 'react'

import { getMemoryDocs, type MemoryDocs, saveMemoryFile } from '@/api/memory'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'

import { useMemoryDraft, useMemoryNote } from './hooks'
import { MemoryPane } from './pane'
import { resolveTarget } from './select'
import { DocRow } from './sidebar'
import { MemoryTree } from './tree'
import type { MemorySelection } from './types'

/**
 * MEMORY PAGE — every file the Bot works from, editable in place.
 *
 * The four context documents (`SOUL.md`, `AGENTS.md`, `memories/USER.md`,
 * `memories/MEMORY.md`) sit at the top; notes follow as a folder tree mirroring
 * `notes/` on disk. Saves are explicit (button or Ctrl/Cmd+S) and land atomically,
 * so a half-typed draft never becomes the Bot's memory. `notes/docs/` shows a lock:
 * those are extracted source documents the memory conventions keep read-only.
 */
export function MemoryView() {
  const { t } = useI18n()
  const m = t.memoryPanel
  const [docs, setDocs] = useState<MemoryDocs | null>(null)
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(true)
  const [selection, setSelection] = useState<MemorySelection | null>(null)
  // Text this session already saved, so the editor's baseline moves with it instead of
  // waiting for a refetch that would blank the pane.
  const [saved, setSaved] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    setLoading(true)
    setFailed(false)

    try {
      const next = await getMemoryDocs()
      setDocs(next)
      // Land on the document that shapes the Bot's identity, not an empty pane.
      setSelection(current => current ?? { kind: 'doc', id: 'soul' })
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const notePath = selection?.kind === 'note' ? selection.path : null
  const { failed: noteFailed, note } = useMemoryNote(notePath)
  const target = resolveTarget(docs, selection, note, noteFailed)
  const path = target?.path ?? ''
  const baseline = saved[path] ?? target?.baseline ?? ''

  const onSaved = useCallback(
    async (content: string) => {
      if (!path) {
        return
      }

      await saveMemoryFile(path, content)
      setSaved(current => ({ ...current, [path]: content }))
      setDocs(await getMemoryDocs())
    },
    [path]
  )

  const draft = useMemoryDraft({
    baseline,
    editable: Boolean(target?.editable),
    onSaved,
    path
  })

  return (
    <section className="flex h-full min-h-0 flex-col">
      <header className="flex items-start justify-between gap-3 border-b border-(--ui-stroke-tertiary) px-4 py-3">
        <div className="min-w-0">
          <h1 className="text-sm font-semibold">{m.title}</h1>
          <p className="text-(--ui-text-tertiary) mt-0.5 truncate text-xs">{m.subtitle}</p>
        </div>
        <Button disabled={loading} onClick={() => void load()} size="sm" variant="ghost">
          {m.refresh}
        </Button>
      </header>

      {failed ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
          <p className="text-sm">{m.loadFailed}</p>
          <Button onClick={() => void load()} size="sm" variant="outline">
            {m.retry}
          </Button>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <aside className="w-64 shrink-0 overflow-y-auto border-r border-(--ui-stroke-tertiary) py-2">
            <p className="text-(--ui-text-tertiary) px-3 py-1 text-[11px] font-medium tracking-wide uppercase">
              {m.documents}
            </p>
            {(docs?.files ?? []).map(file => (
              <DocRow
                active={selection?.kind === 'doc' && selection.id === file.id}
                dirty={draft.dirty && path === file.path}
                key={file.id}
                label={file.path}
                meta={file.exists ? m.bytes(file.bytes) : m.missing}
                onSelect={() => setSelection({ kind: 'doc', id: file.id })}
              />
            ))}
            <p className="text-(--ui-text-tertiary) mt-3 px-3 py-1 text-[11px] font-medium tracking-wide uppercase">
              {m.notes}
            </p>
            {docs?.notes.length === 0 && <p className="text-(--ui-text-tertiary) px-3 py-1 text-xs">{m.noNotes}</p>}
            <MemoryTree
              entries={docs?.notes ?? []}
              onSelect={next => setSelection({ kind: 'note', path: next })}
              selectionPath={notePath}
            />
          </aside>

          <MemoryPane
            draft={draft}
            loading={loading && !docs}
            noteFailed={noteFailed}
            selection={selection}
            target={target}
          />
        </div>
      )}
    </section>
  )
}
