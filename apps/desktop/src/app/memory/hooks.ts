import { useCallback, useEffect, useState } from 'react'

import { getMemoryNote } from '@/api/memory'

export interface NoteBody {
  content: string
  path: string
  too_large: boolean
}

/** Fetch the selected note's text; a stale response never overwrites a newer selection. */
export function useMemoryNote(path: string | null): { failed: boolean; note: NoteBody | null } {
  const [note, setNote] = useState<NoteBody | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!path) {
      setNote(null)
      setFailed(false)

      return
    }

    let cancelled = false
    setNote(null)
    setFailed(false)
    getMemoryNote(path)
      .then(result => {
        if (!cancelled) {
          setNote({ content: result.content, path: result.path, too_large: result.too_large })
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true)
        }
      })

    return () => {
      cancelled = true
    }
  }, [path])

  return { failed, note }
}

/** Draft state for the open file. Drafts are keyed by path so switching files keeps
 * unsaved work instead of silently dropping it; nothing is written until `save`. */
export interface MemoryDraft {
  change: (next: string) => void
  dirty: boolean
  failed: boolean
  revert: () => void
  save: () => Promise<void>
  savedPath: string | null
  saving: boolean
  value: string
}

export function useMemoryDraft({
  baseline,
  editable,
  onSaved,
  path
}: {
  baseline: string
  editable: boolean
  onSaved: (content: string) => Promise<void> | void
  path: string
}): MemoryDraft {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)
  const [savedPath, setSavedPath] = useState<string | null>(null)

  useEffect(() => {
    if (!savedPath) {
      return
    }

    const timer = setTimeout(() => setSavedPath(null), 2000)

    return () => clearTimeout(timer)
  }, [savedPath])

  const value = drafts[path] ?? baseline
  const dirty = drafts[path] !== undefined && drafts[path] !== baseline

  const change = useCallback(
    (next: string) => {
      setSavedPath(null)
      setDrafts(current => ({ ...current, [path]: next }))
    },
    [path]
  )

  const revert = useCallback(() => {
    setDrafts(current => {
      const next = { ...current }
      delete next[path]

      return next
    })
    setFailed(false)
  }, [path])

  const save = useCallback(async () => {
    const draft = drafts[path]

    if (!path || draft === undefined || !editable || saving) {
      return
    }

    setSaving(true)
    setFailed(false)

    try {
      await onSaved(draft)
      setDrafts(current => {
        const next = { ...current }
        delete next[path]

        return next
      })
      setSavedPath(path)
    } catch {
      setFailed(true)
    } finally {
      setSaving(false)
    }
  }, [drafts, editable, onSaved, path, saving])

  return { change, dirty, failed, revert, save, savedPath, saving, value }
}
