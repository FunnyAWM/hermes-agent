import { useEffect, useRef } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'

/**
 * The editing pane: one file, its draft, and the three states that stop an edit
 * (too large, read-only import, load failure). Save is explicit — nothing here
 * writes on blur or on a timer, because these files are the Bot's own memory.
 */
export function MemoryEditor({
  dirty,
  editable,
  missing,
  onChange,
  onRevert,
  onSave,
  path,
  saveError,
  saved,
  saving,
  tooLarge,
  value
}: {
  dirty: boolean
  editable: boolean
  missing: boolean
  onChange: (next: string) => void
  onRevert: () => void
  onSave: () => void
  path: string
  saveError: boolean
  saved: boolean
  saving: boolean
  tooLarge: boolean
  value: string
}) {
  const { t } = useI18n()
  const m = t.memoryPanel
  const area = useRef<HTMLTextAreaElement>(null)
  const canSave = editable && dirty && !saving

  // Ctrl/Cmd+S saves the draft instead of opening the browser's save dialog.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()

        if (canSave) {
          onSave()
        }
      }
    }

    window.addEventListener('keydown', handler)

    return () => window.removeEventListener('keydown', handler)
  }, [canSave, onSave])

  return (
    <main className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-(--ui-stroke-tertiary) px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-[11px] text-(--ui-text-tertiary)">{path}</span>
          {dirty && <span className="text-(--ui-text-secondary) shrink-0 text-[11px]">{m.unsaved}</span>}
          {!dirty && saved && <span className="text-(--ui-text-tertiary) shrink-0 text-[11px]">{m.saved}</span>}
          {!editable && <span className="text-(--ui-text-tertiary) shrink-0 text-[11px]">{m.readOnly}</span>}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {saveError && <span className="text-xs text-destructive">{m.saveFailed}</span>}
          <Button disabled={!dirty || saving} onClick={onRevert} size="sm" variant="ghost">
            {m.revert}
          </Button>
          <Button disabled={!canSave} onClick={onSave} size="sm" variant="secondary">
            {saving ? m.saving : m.save}
          </Button>
        </div>
      </div>

      {tooLarge ? (
        <p className="p-4 text-xs">{m.tooLarge}</p>
      ) : editable ? (
        <>
          {missing && <p className="px-4 pt-3 text-[11px] text-(--ui-text-tertiary)">{m.missingHint}</p>}
          <textarea
            aria-label={path}
            className="min-h-0 flex-1 resize-none bg-transparent p-4 font-mono text-xs leading-relaxed outline-none"
            onChange={event => onChange(event.target.value)}
            ref={area}
            spellCheck={false}
            value={value}
          />
        </>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto p-4">
          <pre className="font-mono text-xs leading-relaxed break-words whitespace-pre-wrap">{value}</pre>
        </div>
      )}
    </main>
  )
}
