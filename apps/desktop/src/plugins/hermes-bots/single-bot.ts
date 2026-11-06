/**
 * Fork product rule: the Bots pane drives exactly ONE assistant.
 *
 * The mobile client has a single assistant with one conversation and a
 * user-set name (PRD_ANDROID 聊 03 / 设 07). The roster is reduced to the
 * default profile; every other profile still exists and keeps working, it is
 * just not offered here.
 */

import { isDefaultBot } from './data'
import type { RosterRow } from './types'

export function singleBotRoster(roster: RosterRow[]): RosterRow[] {
  const bot = roster.find(isDefaultBot)

  return bot ? [bot] : []
}
