import { tombstoneSessions, untombstoneSessions } from './session-removal'

// Cross-window session-list sync. Each desktop window is its own renderer
// process with its own gateway socket and session store, so a mutation in one
// (e.g. a new chat started in the compact pop-out) never reaches another
// window. This bus pings every window to re-pull the shared session list; the
// data already lives in the backend, the other window just doesn't know to look.
const CHANNEL = 'hermes:sessions'

/**
 * What the posting window did to the shared list. `removed` / `released` carry
 * the ids because the optimistic tombstone that keeps a just-deleted row out of
 * the sender's list is renderer-local: without one, the receiver's own refresh
 * re-admits the row through its keep set (pinned, selected, working, tiled), so
 * the delete looks like it never happened in the other window.
 */
export interface SessionsChanged {
  /** Ids another window restored/unarchived: lift their tombstones here too. */
  released?: Array<null | string | undefined>
  /** Ids another window deleted/archived: tombstone them here too. */
  removed?: Array<null | string | undefined>
}

const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(CHANNEL)

// Mirror the sender's removal into this window's tombstones. Registered at
// module load — before any subscriber — so every handler runs against the
// tombstones already armed (its refresh would otherwise re-admit the row and
// the next page would have to filter it out). A plain `1` from an older
// renderer decodes as "nothing to remove", so a mid-update window still pings.
channel?.addEventListener('message', (event: MessageEvent) => {
  const change = (typeof event.data === 'object' && event.data !== null ? event.data : {}) as SessionsChanged

  if (change.removed?.length) {
    tombstoneSessions(change.removed)
  }

  if (change.released?.length) {
    untombstoneSessions(change.released)
  }
})

// A window that mutated the session list (created / titled a chat) tells the
// others to refresh. A BroadcastChannel never delivers to its own poster, so the
// caller refreshes locally as it already does.
export function broadcastSessionsChanged(change?: SessionsChanged): void {
  channel?.postMessage(change ?? {})
}

export function onSessionsChanged(handler: () => void): () => void {
  if (!channel) {
    return () => {}
  }

  channel.addEventListener('message', handler)

  return () => channel.removeEventListener('message', handler)
}
