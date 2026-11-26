import { useCallback, useState } from 'react'

import {
  linkCreateChain,
  linkDisconnect,
  linkErrorOf,
  linkPair,
  linkResetChain,
  linkServe,
  type LinkStatus,
  linkSyncNow
} from '@/api/kourichat-link'
import { updateLinkSettings } from '@/api/kourichat-link'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { type ProfileScope } from '@/hermes'
import { useI18n } from '@/i18n'
import { notify } from '@/store/notifications'

import { invalidateLink, useLinkStatus } from './link-data'

interface LinkTabProps {
  /** The (connection, profile) scope every read and write routes to. */
  profile: ProfileScope
}

const WORDS_PER_ROW = 6

/** THE Link tab: KouriChat Link device-chain membership for the scoped profile —
 *  pair with the Android client's master, run this device as master, and sync
 *  the shared Markdown whitelist on demand. The heavy lifting lives in the
 *  backend (`hermes sync` CLI drives the same manager); this surface only
 *  reads status and posts operations. */
export function LinkTab({ profile }: LinkTabProps) {
  const { t } = useI18n()
  const status = useLinkStatus(profile)
  const [busy, setBusy] = useState(false)
  const [wordsDialog, setWordsDialog] = useState<string[] | null>(null)
  const [pairDialog, setPairDialog] = useState(false)

  const run = useCallback(
    async (action: () => Promise<void>) => {
      setBusy(true)

      try {
        await action()
      } catch (error) {
        const link = linkErrorOf(error)
        notify({ kind: 'error', message: `${link.code}: ${link.message}` })
      } finally {
        setBusy(false)
      }
    },
    []
  )

  const onCreateChain = () =>
    run(async () => {
      const { words } = await linkCreateChain(profile)
      await invalidateLink(profile)
      setWordsDialog(words)
    })

  const onSyncNow = () =>
    run(async () => {
      const outcome = await linkSyncNow(profile)
      await invalidateLink(profile)
      notify({ message: t.linkSync.syncedNow(outcome.records, outcome.applied) })
    })

  const onServe = (start: boolean) =>
    run(async () => {
      await linkServe(start, profile)
      await invalidateLink(profile)
      notify({ message: start ? t.linkSync.serveStarted : t.linkSync.serveStopped })
    })

  const onDisconnect = () =>
    run(async () => {
      await linkDisconnect(profile)
      await invalidateLink(profile)
      notify({ message: t.linkSync.leftChain })
    })

  const onResetChain = () =>
    run(async () => {
      await linkResetChain(profile)
      await invalidateLink(profile)
      notify({ message: t.linkSync.chainReset })
    })

  if (!status) {
    return <p className="text-text-secondary px-1 py-4 text-sm">{t.linkSync.loading}</p>
  }

  return (
    <div className="flex flex-col gap-4 pb-6">
      {status.paired ? (
        <>
          <PairedCard
            busy={busy}
            onDisconnect={onDisconnect}
            onResetChain={onResetChain}
            onServe={onServe}
            onSyncNow={onSyncNow}
            status={status}
          />
          <LinkSettingsSection
            busy={busy}
            // Remount when the server values move: the form seeds its state once,
            // so a refresh that changes them has to re-seed it.
            key={`${status.auto_sync_minutes}:${status.listen_port}`}
            profile={profile}
            status={status}
          />
        </>
      ) : (
        <UnpairedCard busy={busy} onCreateChain={onCreateChain} onPair={() => setPairDialog(true)} />
      )}

      <WordsDialog onClose={() => setWordsDialog(null)} words={wordsDialog} />
      <PairDialog
        busy={busy}
        onClose={() => setPairDialog(false)}
        onPair={words =>
          run(async () => {
            await linkPair(null, words, profile)
            await invalidateLink(profile)
            setPairDialog(false)
            notify({ message: t.linkSync.pairOk })
          })
        }
        open={pairDialog}
      />
    </div>
  )
}

// ── paired state ─────────────────────────────────────────────────────────────────

function PairedCard({
  busy,
  onDisconnect,
  onResetChain,
  onServe,
  onSyncNow,
  status
}: {
  busy: boolean
  onDisconnect: () => void
  onResetChain: () => void
  onServe: (start: boolean) => void
  onSyncNow: () => void
  status: LinkStatus
}) {
  const { t } = useI18n()
  const fingerprint = status.pinned_fingerprint ? `${status.pinned_fingerprint.slice(0, 16)}…` : '—'
  const lastSync = status.last_sync_at_ms ? new Date(status.last_sync_at_ms).toLocaleString() : t.linkSync.never

  return (
    <section className="border-border bg-surface rounded-lg border p-4">
      <header className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium">{t.linkSync.pairedTitle}</h3>
          <p className="text-text-secondary text-xs">
            {status.role === 'master' ? t.linkSync.roleMaster : t.linkSync.roleSecondary}
            {status.serving ? ` · ${t.linkSync.servingOn}` : ''}
          </p>
        </div>
        <Button disabled={busy} onClick={onSyncNow} size="sm">
          {t.linkSync.syncNow}
        </Button>
      </header>
      <dl className="text-text-secondary grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        {status.role === 'secondary' && <Detail label={t.linkSync.masterEndpoint} value={status.master_endpoint || '—'} />}
        {status.role === 'master' && <Detail label={t.linkSync.listenPort} value={String(status.listen_port)} />}
        <Detail label={t.linkSync.fingerprint} value={fingerprint} />
        <Detail label={t.linkSync.tracked} value={`${status.tracked_files} / ${status.syncable_files}`} />
        <Detail label={t.linkSync.appliedArchived} value={`${status.applied_count} / ${status.archived_count}`} />
        <Detail label={t.linkSync.lastSync} value={lastSync} />
      </dl>
      {status.last_sync_error && <p className="text-danger mt-2 text-xs">{status.last_sync_error}</p>}
      <footer className="mt-3 flex gap-2">
        {status.role === 'master' && (
          <Button disabled={busy} onClick={() => onServe(!status.serving)} size="sm" variant="outline">
            {status.serving ? t.linkSync.serveStop : t.linkSync.serveStart}
          </Button>
        )}
        {status.role === 'secondary' && (
          <Button disabled={busy} onClick={onDisconnect} size="sm" variant="outline">
            {t.linkSync.disconnect}
          </Button>
        )}
        {status.role === 'master' && (
          <Button disabled={busy} onClick={onResetChain} size="sm" variant="outline">
            {t.linkSync.resetChain}
          </Button>
        )}
      </footer>
    </section>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="shrink-0">{label}</dt>
      <dd className="truncate font-mono">{value}</dd>
    </>
  )
}

// ── settings ─────────────────────────────────────────────────────────────────────

/** The kourichat_link config keys a user edits from the UI: the periodic-sync interval
 *  and, for a master, the TLS listen port (write path: PUT /api/kourichat-link/settings). */
function LinkSettingsSection({ busy, profile, status }: { busy: boolean; profile: ProfileScope; status: LinkStatus }) {
  const { t } = useI18n()
  const [autoSyncMinutes, setAutoSyncMinutes] = useState(String(status.auto_sync_minutes))
  const [listenPort, setListenPort] = useState(String(status.listen_port))

  const intervals = [0, 15, 30, 60, 360, 1440]

  const onSave = () =>
    run_settings(async () => {
      const port = Number(listenPort)

      if (status.role === 'master' && (!Number.isInteger(port) || port < 1 || port > 65535)) {
        notify({ kind: 'error', message: t.linkSync.portInvalid })

        return
      }

      await updateLinkSettings(
        { auto_sync_minutes: Number(autoSyncMinutes), listen_port: status.role === 'master' ? port : null },
        profile
      )
      await invalidateLink(profile)
      notify({ message: t.linkSync.settingsSaved })
    })

  // The section reuses the tab's error/notify envelope; a tiny local runner keeps
  // LinkTab's own `busy` untouched so a settings save never disables the sync buttons.
  function run_settings(action: () => Promise<void>) {
    void action().catch(error => {
      const link = linkErrorOf(error)
      notify({ kind: 'error', message: `${link.code}: ${link.message}` })
    })
  }

  return (
    <section className="border-border bg-surface rounded-lg border p-4">
      <h3 className="text-sm font-medium">{t.linkSync.settingsTitle}</h3>
      <div className="mt-3 flex flex-wrap items-end gap-4">
        <label className="text-xs">
          {t.linkSync.autoSyncLabel}
          <select
            className="border-border bg-surface mt-1 block rounded-md border px-2 py-1 text-sm"
            onChange={event => setAutoSyncMinutes(event.target.value)}
            value={autoSyncMinutes}
          >
            {intervals.map(minutes => (
              <option key={minutes} value={minutes}>
                {minutes === 0 ? t.linkSync.autoSyncOff : t.linkSync.autoSyncEvery(minutes)}
              </option>
            ))}
          </select>
        </label>
        {status.role === 'master' && (
          <label className="text-xs">
            {t.linkSync.listenPortLabel}
            <input
              className="border-border bg-surface mt-1 block w-28 rounded-md border px-2 py-1 font-mono text-sm"
              inputMode="numeric"
              onChange={event => setListenPort(event.target.value)}
              value={listenPort}
            />
          </label>
        )}
        <Button disabled={busy} onClick={onSave} size="sm" variant="outline">
          {t.linkSync.saveSettings}
        </Button>
      </div>
      {status.role === 'master' && <p className="text-text-secondary mt-2 text-xs">{t.linkSync.portHint}</p>}
    </section>
  )
}

// ── unpaired state ───────────────────────────────────────────────────────────────

function UnpairedCard({ busy, onCreateChain, onPair }: { busy: boolean; onCreateChain: () => void; onPair: () => void }) {
  const { t } = useI18n()

  return (
    <section className="border-border bg-surface rounded-lg border p-4">
      <h3 className="text-sm font-medium">{t.linkSync.notPairedTitle}</h3>
      <p className="text-text-secondary mt-1 text-xs">{t.linkSync.notPairedDesc}</p>
      <div className="mt-3 flex gap-2">
        <Button disabled={busy} onClick={onPair} size="sm">
          {t.linkSync.pairWithMaster}
        </Button>
        <Button disabled={busy} onClick={onCreateChain} size="sm" variant="outline">
          {t.linkSync.createChain}
        </Button>
      </div>
    </section>
  )
}

// ── dialogs ──────────────────────────────────────────────────────────────────────

function WordsDialog({ onClose, words }: { onClose: () => void; words: string[] | null }) {
  const { t } = useI18n()

  return (
    <Dialog onOpenChange={open => !open && onClose()} open={words !== null}>
      <DialogContent>
        <DialogTitle>{t.linkSync.chainCreated}</DialogTitle>
        <p className="text-text-secondary text-xs">{t.linkSync.wordsWarning}</p>
        <div className="bg-surface grid grid-cols-3 gap-x-4 gap-y-1 rounded-md p-3 font-mono text-sm" data-testid="link-words">
          {(words ?? []).map((word, index) => (
            <span key={`${word}-${index}`}>{`${index + 1}. ${word}`}</span>
          ))}
        </div>
        <div className="flex justify-end gap-2">
          <Button
            onClick={() => {
              void navigator.clipboard.writeText((words ?? []).join(' '))
              notify({ message: t.linkSync.wordsCopied })
            }}
            size="sm"
            variant="outline"
          >
            {t.linkSync.copyWords}
          </Button>
          <Button onClick={onClose} size="sm">
            {t.linkSync.done}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** 24-word entry. The master is discovered on the LAN (same as the Android client), so
 *  there is no address to type. The words are validated by the backend (checksum fail →
 *  HM-6002 envelope), the UI just collects them. */
function PairDialog({
  busy,
  onClose,
  onPair,
  open
}: {
  busy: boolean
  onClose: () => void
  onPair: (words: string[]) => void
  open: boolean
}) {
  const { t } = useI18n()
  const [words, setWords] = useState('')
  const wordCount = words.trim().split(/\s+/).filter(Boolean).length

  return (
    <Dialog onOpenChange={next => !next && onClose()} open={open}>
      <DialogContent>
        <DialogTitle>{t.linkSync.pairWithMaster}</DialogTitle>
        <p className="text-text-secondary text-xs">{t.linkSync.pairDiscoverHint}</p>
        <label className="text-xs">
          {t.linkSync.wordsLabel}
          <textarea
            className="border-border bg-surface mt-1 h-24 w-full resize-none rounded-md border px-2 py-1 font-mono text-sm"
            onChange={event => setWords(event.target.value)}
            placeholder={t.linkSync.wordsPlaceholder}
          />
        </label>
        <p className="text-text-secondary text-xs">{t.linkSync.wordCount(wordCount)}</p>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose} size="sm" variant="outline">
            {t.linkSync.cancel}
          </Button>
          <Button
            disabled={busy || wordCount !== 24}
            onClick={() => onPair(words.trim().split(/\s+/))}
            size="sm"
          >
            {t.linkSync.pair}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
