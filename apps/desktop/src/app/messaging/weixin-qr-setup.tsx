import { pairWeixin } from '@hermes/shared/weixin-onboarding'
import { useEffect, useRef, useState } from 'react'

import { capabilityScoped, hermesApi, type ProfileScope } from '@/api/client'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'

export function WeixinQrSetup({ scope, onSaved }: { scope: ProfileScope; onSaved: (restartNeeded: boolean) => void }) {
  const { locale, t } = useI18n()
  const zh = locale.startsWith('zh')
  const flow = useRef<AbortController | null>(null)
  const [busy, setBusy] = useState(false)
  const [image, setImage] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => () => flow.current?.abort(), [])

  async function start() {
    if (flow.current) {
      return
    }

    const controller = new AbortController()
    const owner = capabilityScoped(scope)
    flow.current = controller
    setBusy(true)
    setMessage('')

    try {
      const result = await pairWeixin(
        (path, method) => hermesApi({ ...owner, path, method }),
        async payload => {
          const url = await (await import('qrcode')).toDataURL(payload, { width: 224, margin: 2 })

          if (!controller.signal.aborted) {
            setImage(url)
          }
        },
        controller.signal
      )

      if (controller.signal.aborted) {
        return
      }

      if (result.status !== 'connected') {
        throw new Error(zh ? '二维码已失效，请重新扫码。' : 'Pairing failed. Start a new QR setup.')
      }

      setMessage(zh ? '微信连接配置已保存。' : 'WeChat connection saved.')
      onSaved(result.needs_restart)
    } catch (error) {
      if (!controller.signal.aborted) {
        setMessage(String(error))
      }
    } finally {
      if (flow.current === controller) {
        flow.current = null
        setBusy(false)
        setImage('')
      }
    }
  }

  return (
    <section className="grid justify-items-start gap-2">
      <Button onClick={() => (busy ? flow.current?.abort('cancel') : void start())} size="sm">
        {busy ? t.common.cancel : zh ? '用微信扫码连接' : 'Connect with WeChat QR'}
      </Button>
      {busy && (
        <p role="status">
          {zh
            ? '请用微信扫码并确认，随后会自动保存连接。'
            : 'Scan and confirm in WeChat to save the connection automatically.'}
        </p>
      )}
      {image && <img alt={zh ? '微信绑定二维码' : 'WeChat setup QR code'} height={224} src={image} width={224} />}
      {message && <p role="status">{message}</p>}
    </section>
  )
}
