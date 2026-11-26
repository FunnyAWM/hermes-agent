// @vitest-environment jsdom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { WeixinOnboardingPanel } from './ChannelsWeixin'

const fetchJSON = vi.hoisted(() => vi.fn())
vi.mock('@/lib/api', () => ({ fetchJSON }))
vi.mock('qrcode', () => ({ toDataURL: async (payload: string) => 'data:image/png,' + payload }))

it('displays the QR and refreshes the selected profile on phone confirmation', async () => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  vi.useFakeTimers()
  const container = document.createElement('div')
  const root = createRoot(container)
  const saved = vi.fn()
  fetchJSON.mockResolvedValueOnce({ status: 'waiting', pairing_id: 'pair', qr_payload: 'wechat-login' })
    .mockResolvedValueOnce({ status: 'connected', needs_restart: true })
  try {
    await act(async () => root.render(<WeixinOnboardingPanel profile="worker" onSaved={saved} />))
    await act(async () => { container.querySelector('button')!.click() })
    expect(container.querySelector('img')?.getAttribute('src')).toBe('data:image/png,wechat-login')
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(saved).toHaveBeenCalledWith(true)
    expect(fetchJSON.mock.calls.map(([url]) => url)).toEqual([
      '/api/messaging/weixin/onboarding/start?profile=worker',
      '/api/messaging/weixin/onboarding/pair?profile=worker'
    ])
    expect(container.querySelector('img')).toBeNull()
  } finally {
    await act(async () => root.unmount())
    vi.useRealTimers()
  }
})
