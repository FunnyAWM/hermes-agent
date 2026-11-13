import { capabilityScoped, type ProfileScope } from './client'

/** One context document the Bot loads into a turn (`/api/memory/docs`). */
export interface MemoryDocument {
  id: 'soul' | 'agents' | 'user' | 'memory'
  /** Path relative to HERMES_HOME. */
  path: string
  exists: boolean
  content: string
  bytes: number
  too_large: boolean
  editable: boolean
}

/** A note under `notes/` — listed by path, read on demand. */
export interface MemoryNoteEntry {
  path: string
  bytes: number
  updated_at_ms: number
  /** False for `notes/docs/` — imported source text the panel never rewrites. */
  editable: boolean
}

export interface MemoryDocs {
  ok: boolean
  home: string
  files: MemoryDocument[]
  notes: MemoryNoteEntry[]
}

export interface MemoryNoteBody {
  ok: boolean
  path: string
  content: string
  bytes: number
  too_large: boolean
}

export interface MemorySaved {
  ok: boolean
  path: string
  bytes: number
  updated_at_ms: number
}

const MEMORY_PATH = '/api/memory'

/** The four context files plus the notes index, scoped to the active profile. */
export function getMemoryDocs(profile?: ProfileScope): Promise<MemoryDocs> {
  return window.hermesDesktop.api<MemoryDocs>({
    ...capabilityScoped(profile),
    path: `${MEMORY_PATH}/docs`
  })
}

/** One note's text. The backend refuses anything outside `notes/**.md`. */
export function getMemoryNote(path: string, profile?: ProfileScope): Promise<MemoryNoteBody> {
  return window.hermesDesktop.api<MemoryNoteBody>({
    ...capabilityScoped(profile),
    path: `${MEMORY_PATH}/note?path=${encodeURIComponent(path)}`
  })
}

/** Save one context file or note. The backend's whitelist decides what is writable. */
export function saveMemoryFile(
  path: string,
  content: string,
  profile?: ProfileScope
): Promise<MemorySaved> {
  return window.hermesDesktop.api<MemorySaved>({
    ...capabilityScoped(profile),
    method: 'POST',
    path: `${MEMORY_PATH}/doc`,
    body: { path, content }
  })
}
