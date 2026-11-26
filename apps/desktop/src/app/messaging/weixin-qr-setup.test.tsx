import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { WeixinQrSetup } from './weixin-qr-setup'

const request = vi.hoisted(() => vi.fn())
vi.mock('@/api/client', async () => ({ ...(await vi.importActual('@/api/client')), hermesApi: request }))
vi.mock('qrcode', () => ({ toDataURL: async (text: string) => 'data:image/png,' + text }))
afterEach(cleanup)

it('renders the scannable payload on the page and applies the confirmed connection automatically', async () => {
  request
    .mockResolvedValueOnce({ pairing_id: 'id', status: 'waiting', qr_payload: 'scan-me' })
    .mockResolvedValueOnce({ pairing_id: 'id', status: 'connected', needs_restart: false })
  const saved = vi.fn()
  render(<WeixinQrSetup onSaved={saved} scope={{ profile: 'work', connectionId: 'remote' }} />)
  fireEvent.click(screen.getByText('Connect with WeChat QR'))
  expect((await screen.findByAltText('WeChat setup QR code')).getAttribute('src')).toBe('data:image/png,scan-me')
  await waitFor(() => expect(saved).toHaveBeenCalledWith(false), { timeout: 3500 })
  expect(screen.queryByRole('img')).toBeNull()
  expect(request.mock.calls.map(([args]) => [args.method, args.profile, args.connectionId])).toEqual([
    ['POST', 'work', 'remote'],
    ['GET', 'work', 'remote']
  ])
})
