import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionInfo } from '@/types/hermes'

type Listener = (event: MessageEvent) => void

/** Same-origin BroadcastChannel never delivers to the posting window; a second
 *  instance in the same test is exactly "another desktop window". */
class FakeChannel {
  static instances = new Set<FakeChannel>()

  listeners = new Set<Listener>()

  constructor(readonly name: string) {
    FakeChannel.instances.add(this)
  }

  addEventListener(_type: 'message', listener: Listener) {
    this.listeners.add(listener)
  }

  removeEventListener(_type: 'message', listener: Listener) {
    this.listeners.delete(listener)
  }

  postMessage(data: unknown) {
    for (const channel of FakeChannel.instances) {
      if (channel === this) {
        continue
      }

      for (const listener of [...channel.listeners]) {
        listener({ data } as MessageEvent)
      }
    }
  }

  close() {}
}

vi.stubGlobal('BroadcastChannel', FakeChannel)

const { broadcastSessionsChanged, onSessionsChanged } = await import('./session-sync')
const { $removedSessionIds, tombstoneSessions } = await import('./session-removal')
const { mergeSessionPage } = await import('./session')

const row = (id: string): SessionInfo =>
  ({
    ended_at: null,
    id,
    input_tokens: 0,
    is_active: false,
    last_active: 1000,
    message_count: 3,
    model: 'm',
    output_tokens: 0,
    preview: 'hey',
    profile: 'default',
    source: 'desktop',
    started_at: 900,
    title: `Chat ${id}`
  }) as SessionInfo

describe('session-sync', () => {
  let stops: Array<() => void> = []

  const anotherWindow = () => new FakeChannel('hermes:sessions')

  beforeEach(() => {
    for (const stop of stops) {
      stop()
    }

    stops = []
    $removedSessionIds.set(new Set())
  })

  function listen(handler = vi.fn()) {
    stops.push(onSessionsChanged(handler))

    return handler
  }

  it('tombstones the ids another window deleted, then pings the list', () => {
    const handler = listen()

    anotherWindow().postMessage({ removed: ['tip-1', 'root-1'] })

    expect(handler).toHaveBeenCalledTimes(1)
    expect([...$removedSessionIds.get()]).toEqual(['tip-1', 'root-1'])
  })

  it('lifts the tombstones another window released (unarchive)', () => {
    tombstoneSessions(['tip-1', 'root-1'])
    const handler = listen()

    anotherWindow().postMessage({ released: ['root-1'] })

    expect(handler).toHaveBeenCalledTimes(1)
    expect([...$removedSessionIds.get()]).toEqual(['tip-1'])
  })

  it('a plain refresh ping from an older window still refreshes, arming nothing', () => {
    const handler = listen()

    anotherWindow().postMessage(1)

    expect(handler).toHaveBeenCalledTimes(1)
    expect($removedSessionIds.get().size).toBe(0)
  })

  it('posts the change to the other window and leaves this window alone', () => {
    const sender = listen()
    const peer = anotherWindow()
    const peerHandler = vi.fn()
    peer.addEventListener('message', peerHandler)

    broadcastSessionsChanged({ removed: ['tip-1'] })

    expect(peerHandler).toHaveBeenCalledTimes(1)
    expect((peerHandler.mock.calls[0][0] as MessageEvent).data).toEqual({ removed: ['tip-1'] })
    // The poster evicts optimistically itself; the bus must not re-tombstone it.
    expect($removedSessionIds.get().size).toBe(0)
    expect(sender).not.toHaveBeenCalled()
  })

  it('keeps the tombstone armed by another window ahead of the keep set', () => {
    // The receiving window may have the row pinned / selected / working — its
    // refresh keep set names it even though the backend page no longer serves
    // it. Without the bus-armed tombstone the row resurrects (#118156).
    const pinned = row('pinned-1')
    expect(mergeSessionPage([pinned], [], new Set(['pinned-1']))).toEqual([pinned])

    anotherWindow().postMessage({ removed: ['pinned-1'] })

    expect(mergeSessionPage([pinned], [], new Set(['pinned-1']))).toEqual([])
  })
})
