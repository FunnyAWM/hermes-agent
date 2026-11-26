import { expect, it } from 'vitest'

import { BUILTIN_THEMES } from './presets'

// T-11: a default-config desktop must not fetch remote stylesheets. The theme
// loader injects `typography.fontUrl` as a <link>, so any built-in theme that
// carries one makes the app reach fonts.googleapis.com on first paint.
it('no built-in theme declares a remote font stylesheet', () => {
  const offenders = Object.values(BUILTIN_THEMES).flatMap((theme) => {
    const url = theme.typography?.fontUrl

    return url && /^https?:\/\//i.test(url) ? [`${theme.name} -> ${url}`] : []
  })

  expect(offenders).toEqual([])
})
