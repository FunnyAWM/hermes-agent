/** What the Memory page can show: one of the four context files, or one note. */
export type MemorySelection = { kind: 'doc'; id: string } | { kind: 'note'; path: string }

/** A folder or a file in the notes tree (`notes/<category>/<name>.md`). */
export interface NoteNode {
  /** Segment as it appears on disk, e.g. `work` or `2019-trip.md`. */
  name: string
  /** Path relative to HERMES_HOME — a folder path for groups, a file path for notes. */
  path: string
  children: NoteNode[]
  /** Set on leaves only. */
  entry?: { path: string; bytes: number; editable: boolean }
}
