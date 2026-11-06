import { describe, expect, it } from 'vitest'

import { singleBotRoster } from './single-bot'
import type { RosterRow } from './types'

const bot = (name: string, extra: Partial<RosterRow> = {}): RosterRow => ({ name, ...extra })

describe('singleBotRoster', () => {
  it('keeps only the default profile, in roster order', () => {
    expect(singleBotRoster([bot('researcher'), bot('default'), bot('writer')])).toEqual([bot('default')])
  })

  it('keeps a uniquely named default profile without inventing a row', () => {
    expect(singleBotRoster([bot('researcher')])).toEqual([])
  })

  it('returns an empty roster when the default row is absent', () => {
    expect(singleBotRoster([])).toEqual([])
  })
})
