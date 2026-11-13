import { describe, expect, it } from 'vitest'

import { deriveRosterRows } from './roster-pane-derivation'
import type { BotMeta, GroupChat, RosterRow } from './types'

function derive(roster: RosterRow[], singleBot: boolean) {
  return deriveRosterRows({
    roster,
    allMeta: {} as Record<string, BotMeta>,
    gatewayFilter: 'all',
    query: '',
    activityFilter: 'all',
    rowKindFilter: 'all',
    groupRooms: { Room: { log: [], members: [{ name: 'default' }], watermarks: {} } as GroupChat },
    activeRosterKeys: new Set<string>(),
    gatewayOptions: [],
    activityOf: () => 0,
    isPinned: () => false,
    singleBot
  })
}

describe('deriveRosterRows singleBot', () => {
  it('keeps the one visible row and drops group rooms', () => {
    const single = derive([{ name: 'default' }], true)

    expect(single.visibleRoster.map(bot => bot.name)).toEqual(['default'])
    expect(single.rosterRows.map(row => row.kind)).toEqual(['bot'])
    expect(single.groupNames).toEqual([])
    expect(single.hiddenBots).toEqual([])
  })

  it('leaves the multi-bot presentation untouched when the rule is off', () => {
    const multi = derive([{ name: 'default' }, { name: 'writer' }], false)

    expect(multi.visibleRoster.map(bot => bot.name)).toEqual(['default', 'writer'])
    expect(multi.groupNames).toEqual(['Room'])
  })
})
