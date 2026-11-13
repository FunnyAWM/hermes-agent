import type { MemoryDocs } from '@/api/memory'

import type { MemorySelection } from './types'

/** What the editor needs to know about the current selection. */
export interface MemoryTarget {
  path: string
  baseline: string
  tooLarge: boolean
  editable: boolean
  /** A context file that does not exist yet — saving creates it. */
  missing: boolean
}

/**
 * Collapse the three sources of truth (`docs` for context files, the fetched note body
 * for notes, the selection itself) into one shape the view can render without a chain
 * of ternaries. `null` means the note has not loaded yet.
 */
export function resolveTarget(
  docs: MemoryDocs | null,
  selection: MemorySelection | null,
  note: { content: string; path: string; too_large: boolean } | null,
  noteFailed: boolean
): MemoryTarget | null {
  if (!selection) {
    return null
  }

  if (selection.kind === 'doc') {
    const document = docs?.files.find(file => file.id === selection.id)

    if (!document) {
      return null
    }

    return {
      path: document.path,
      baseline: document.content,
      tooLarge: document.too_large,
      editable: document.editable && !document.too_large,
      missing: !document.exists
    }
  }

  const entry = docs?.notes.find(item => item.path === selection.path)

  if (!note || noteFailed) {
    return {
      path: selection.path,
      baseline: '',
      tooLarge: false,
      editable: false,
      missing: false
    }
  }

  return {
    path: note.path,
    baseline: note.content,
    tooLarge: note.too_large,
    editable: Boolean(entry?.editable) && !note.too_large,
    missing: false
  }
}
