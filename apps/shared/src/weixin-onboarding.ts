export interface WeixinPairing {
  pairing_id: string
  status: 'waiting' | 'saving' | 'connected' | 'cancelled' | 'error'
  qr_payload: string
  needs_restart: boolean
}

export type WeixinRequest = (path: string, method: 'GET' | 'POST' | 'DELETE') => Promise<WeixinPairing>

/** The backend owns confirmation and saving. Poll serially so a late response cannot restore an expired QR. */
export async function pairWeixin(
  request: WeixinRequest,
  showQr: (payload: string) => Promise<void>,
  signal: AbortSignal
): Promise<WeixinPairing> {
  const base = '/api/messaging/weixin/onboarding'
  let state = await request(`${base}/start`, 'POST')
  const path = `${base}/${encodeURIComponent(state.pairing_id)}`
  let shown = ''

  while (state.status === 'waiting' || state.status === 'saving') {
    if (signal.aborted) {
      if (signal.reason === 'cancel') {
        await request(path, 'DELETE')
      }

      signal.throwIfAborted()
    }

    if (state.qr_payload !== shown) {
      shown = state.qr_payload
      await showQr(shown)
    }

    await new Promise(resolve => setTimeout(resolve, 1000))

    if (!signal.aborted) {
      state = await request(path, 'GET')
    }
  }

  return state
}
