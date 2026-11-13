import { capabilityScoped, type ProfileScope } from './client'

/** One profile's KouriChat Link membership, as `GET /api/kourichat-link/status` returns. */
export interface LinkStatus {
  ok: boolean
  role: 'disabled' | 'master' | 'secondary'
  paired: boolean
  device_id: string | null
  master_endpoint: string
  listen_port: number
  auto_sync_minutes: number
  pinned_fingerprint: string | null
  last_sync_at_ms: number | null
  last_sync_error: string | null
  applied_count: number
  archived_count: number
  tracked_files: number
  syncable_files: number
  serving: boolean
}

/** The HM-600x (plus ALREADY_PAIRED / PROFILE_UNKNOWN) envelope sync routes fail with. */
export interface LinkError {
  code: string
  message: string
}

/** FastAPI `detail` for sync failures is the structured envelope; older backends or
 *  proxy layers may surface a plain string. Normalizes both for the UI. */
export function linkErrorOf(error: unknown): LinkError {
  const raw = error instanceof Error ? error.message : String(error ?? '')

  try {
    const parsed = JSON.parse(raw) as { code?: string; message?: string; detail?: LinkError | string }
    const detail = parsed?.detail ?? parsed

    if (detail && typeof detail === 'object' && 'code' in detail) {
      return { code: String(detail.code), message: String(detail.message ?? '') }
    }
  } catch {
    // not a JSON envelope — fall through to the raw text
  }

  return { code: 'UNKNOWN', message: raw }
}

const LINK_PATH = '/api/kourichat-link'

export function getLinkStatus(profile?: ProfileScope): Promise<LinkStatus> {
  return window.hermesDesktop.api<LinkStatus>({
    ...capabilityScoped(profile),
    path: `${LINK_PATH}/status`
  })
}

/** Become the chain master; the 24 words come back exactly once (paper backup). */
export function linkCreateChain(profile?: ProfileScope): Promise<{ ok: boolean; words: string[] }> {
  return window.hermesDesktop.api<{ ok: boolean; words: string[] }>({
    ...capabilityScoped(profile),
    path: `${LINK_PATH}/create-chain`,
    method: 'POST',
    body: {}
  })
}

/** Join a chain as secondary: full negotiation + apply, then the pairing persists
 *  (all-or-nothing). An empty/absent endpoint auto-discovers the master on this LAN. */
export function linkPair(
  endpoint: string | null,
  words: string[],
  profile?: ProfileScope
): Promise<{ ok: boolean; device_id: string; pinned_fingerprint: string; master_endpoint: string }> {
  return window.hermesDesktop.api<{
    ok: boolean
    device_id: string
    pinned_fingerprint: string
    master_endpoint: string
  }>({
    ...capabilityScoped(profile),
    path: `${LINK_PATH}/pair`,
    method: 'POST',
    body: { endpoint: endpoint ?? null, words }
  })
}

/** Sync once: refresh the record log as master, pull + apply as secondary. */
export function linkSyncNow(
  profile?: ProfileScope
): Promise<{ ok: boolean; role: string; records: number; applied: number }> {
  return window.hermesDesktop.api<{ ok: boolean; role: string; records: number; applied: number }>({
    ...capabilityScoped(profile),
    path: `${LINK_PATH}/now`,
    method: 'POST',
    body: {}
  })
}

/** Start/stop the master-role TLS sync server inside the backend process. */
export function linkServe(
  start: boolean,
  profile?: ProfileScope
): Promise<{ ok: boolean; serving: boolean; port: number | null }> {
  return window.hermesDesktop.api<{ ok: boolean; serving: boolean; port: number | null }>({
    ...capabilityScoped(profile),
    path: `${LINK_PATH}/serve`,
    method: 'POST',
    body: { start }
  })
}

/** Leave the chain (secondary): pairing discarded, synced files stay. */
export function linkDisconnect(profile?: ProfileScope): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    ...capabilityScoped(profile),
    path: `${LINK_PATH}/disconnect`,
    method: 'POST',
    body: {}
  })
}

/** Kill the chain (master): stop serving and discard this device's pairing. */
export function linkResetChain(profile?: ProfileScope): Promise<{ ok: boolean }> {
  return window.hermesDesktop.api<{ ok: boolean }>({
    ...capabilityScoped(profile),
    path: `${LINK_PATH}/reset-chain`,
    method: 'POST',
    body: {}
  })
}

/** Persist user-editable sync settings; `null` leaves a key untouched. */
export function updateLinkSettings(
  settings: { listen_port?: number | null; auto_sync_minutes?: number | null },
  profile?: ProfileScope
): Promise<{ ok: boolean; listen_port: number; auto_sync_minutes: number }> {
  return window.hermesDesktop.api<{ ok: boolean; listen_port: number; auto_sync_minutes: number }>({
    ...capabilityScoped(profile),
    path: `${LINK_PATH}/settings`,
    method: 'PUT',
    body: {
      listen_port: settings.listen_port ?? null,
      auto_sync_minutes: settings.auto_sync_minutes ?? null
    }
  })
}
