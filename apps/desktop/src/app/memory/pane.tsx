import { useI18n } from '@/i18n'

import { MemoryEditor } from './editor'
import type { MemoryDraft } from './hooks'
import type { MemoryTarget } from './select'
import type { MemorySelection } from './types'

/** The right-hand pane: one of four states, in the order they can go wrong. */
export function MemoryPane({
  draft,
  loading,
  noteFailed,
  selection,
  target
}: {
  draft: MemoryDraft
  loading: boolean
  noteFailed: boolean
  selection: MemorySelection | null
  target: MemoryTarget | null
}) {
  const { t } = useI18n()
  const m = t.memoryPanel

  if (!selection) {
    return <p className="text-(--ui-text-tertiary) flex-1 p-4 text-xs">{loading ? m.loading : m.selectPrompt}</p>
  }

  if (noteFailed) {
    return <p className="flex-1 p-4 text-xs">{m.noteMissing}</p>
  }

  if (!target) {
    return <p className="text-(--ui-text-tertiary) flex-1 p-4 text-xs">{m.loading}</p>
  }

  return (
    <MemoryEditor
      dirty={draft.dirty}
      editable={target.editable}
      missing={target.missing}
      onChange={draft.change}
      onRevert={draft.revert}
      onSave={() => void draft.save()}
      path={target.path}
      saved={draft.savedPath === target.path}
      saveError={draft.failed}
      saving={draft.saving}
      tooLarge={target.tooLarge}
      value={draft.value}
    />
  )
}
