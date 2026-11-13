import { useQuery } from '@tanstack/react-query'

import { getLinkStatus, type LinkStatus } from '@/api/kourichat-link'
import { type ProfileScope, profileScopeKey } from '@/hermes'
import { queryClient } from '@/lib/query-client'

// The link status lives in the RQ cache like every capability list: a tab switch
// paints the cached copy instantly and a mount fires a deduped background refetch.
// A profile swap globally invalidates (store/profile), so the plain scoped key
// refetches against the new backend automatically.
export const LINK_QUERY_KEY = ['kourichat-link'] as const

export const linkQueryKey = (profile: ProfileScope) => [...LINK_QUERY_KEY, profileScopeKey(profile)]

export function useLinkStatus(profile: ProfileScope): LinkStatus | undefined {
  return useQuery({
    queryKey: linkQueryKey(profile),
    queryFn: () => getLinkStatus(profile),
    staleTime: 0
  }).data
}

export const invalidateLink = (profile: ProfileScope) =>
  queryClient.invalidateQueries({ queryKey: linkQueryKey(profile) })
