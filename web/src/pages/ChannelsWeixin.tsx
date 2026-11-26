import { pairWeixin } from '@hermes/shared/weixin-onboarding'
import { Button } from '@nous-research/ui/ui/components/button'
import { useEffect, useRef, useState } from 'react'
import { fetchJSON } from '@/lib/api'

export function WeixinOnboardingPanel({
  profile,
  onSaved
}: {
  profile: string
  onSaved: (restartNeeded: boolean) => void
}) {
  const flow = useRef<AbortController | null>(null)
  const [busy, setBusy] = useState(false)
  const [image, setImage] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => () => flow.current?.abort(), [])

  async function start() {
    if (flow.current) return
    const controller = new AbortController()
    flow.current = controller
    setBusy(true)
    setMessage('')
    try {
      const result = await pairWeixin(
        (path, method) => fetchJSON(`${path}?profile=${encodeURIComponent(profile)}`, { method }),
        async payload => {
          const url = await (await import('qrcode')).toDataURL(payload, { width: 224, margin: 2 })
          if (!controller.signal.aborted) setImage(url)
        },
        controller.signal
      )
      if (controller.signal.aborted) return
      if (result.status !== 'connected') throw new Error('Pairing failed. Start a new QR setup.')
      setMessage('WeChat connection saved.')
      onSaved(result.needs_restart)
    } catch (error) {
      if (!controller.signal.aborted) setMessage(String(error))
    } finally {
      flow.current = null
      setBusy(false)
      setImage('')
    }
  }

  return (
    <section className="mt-4 grid justify-items-start gap-2">
      <Button size="sm" onClick={() => (busy ? flow.current?.abort('cancel') : void start())}>
        {busy ? 'Cancel' : 'Connect with WeChat QR'}
      </Button>
      {busy && <p role="status">Scan and confirm in WeChat to save the connection automatically.</p>}
      {image && <img alt="WeChat setup QR code" width={224} height={224} src={image} />}
      {message && <p role="status">{message}</p>}
    </section>
  )
}
