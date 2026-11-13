import { useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  dropdownMenuRow,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { ShieldLock } from '@/lib/icons'
import { cn } from '@/lib/utils'
import { confirm } from '@/store/confirm'
import { notifyError } from '@/store/notifications'

import type { ChatBarState } from './types'

const PILL = cn(
  'h-(--composer-control-size) min-w-0 shrink gap-1 rounded-md px-2 text-xs font-normal',
  'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
)

/**
 * Sandbox profile picker in the composer row, left of the model pill. The gate reads
 * `terminal.sandbox.profile` per command, so a pick lands on the next command without
 * restarting the session — the same file the Settings → Workspace → Shell panel writes.
 * Data and the save come from the chat-bar state, so this stays a leaf control.
 */
export function SandboxPill({
  compact = false,
  disabled,
  sandbox
}: {
  compact?: boolean
  disabled: boolean
  sandbox?: ChatBarState['sandbox']
}) {
  const { t } = useI18n()
  const c = t.composer
  const [open, setOpen] = useState(false)

  // Off (or still loading) means a profile pick would change nothing.
  if (!sandbox) {
    return null
  }

  const { active, profiles } = sandbox
  const presetOf = (name: string) => profiles.find(profile => profile.name === name)?.preset ?? name

  const pick = async (name: string) => {
    try {
      await sandbox.onPick(name)
      triggerHaptic('selection')
    } catch (error) {
      notifyError(error, c.sandboxProfileSaveFailed)
    }
  }

  // Auto Review rides in the same picker but is an approval mode, not a sandbox:
  // the profile radio below stays untouched, and switching the toggle off
  // restores the mode it replaced.
  const toggleAutoReview = async (enabled: boolean) => {
    try {
      await sandbox.onToggleAutoReview(enabled)
      triggerHaptic('selection')
    } catch (error) {
      notifyError(error, c.autoReviewSaveFailed)
    }
  }

  const choose = (name: string) => {
    if (presetOf(name) !== 'full-access') {
      void pick(name)

      return
    }

    void confirm({
      destructive: true,
      title: c.sandboxProfileFullAccessTitle,
      description: c.sandboxProfileFullAccessDesc,
      confirmLabel: c.sandboxProfileFullAccessConfirm,
      cancelLabel: c.sandboxProfileCancel
    }).then(ok => {
      if (ok) {
        void pick(name)
      }
    })
  }

  return (
    <DropdownMenu onOpenChange={setOpen} open={open}>
      <Tip label={`${c.sandboxProfile}: ${active}`} side="top">
        <DropdownMenuTrigger asChild>
          <Button
            aria-label={`${c.sandboxProfile}: ${active}`}
            className={compact ? cn(PILL, 'justify-center px-1.5') : PILL}
            disabled={disabled}
            type="button"
            variant="ghost"
          >
            <ShieldLock className="size-3.5 shrink-0 opacity-70" />
            {compact ? null : <span className="max-w-40 truncate">{active}</span>}
          </Button>
        </DropdownMenuTrigger>
      </Tip>
      <DropdownMenuContent align="start" className="w-64 p-0" side="top" sideOffset={8}>
        <DropdownMenuLabel>{c.sandboxProfile}</DropdownMenuLabel>
        <DropdownMenuCheckboxItem
          checked={sandbox.approvalMode === 'auto'}
          className={dropdownMenuRow}
          disabled={disabled}
          onCheckedChange={value => void toggleAutoReview(value === true)}
          // Toggling is a setting, not a pick: leave the menu open so the
          // profile list underneath stays reachable.
          onSelect={event => event.preventDefault()}
        >
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="text-xs text-foreground">{c.autoReview}</span>
            <span className="text-[0.6875rem] leading-snug text-(--ui-text-tertiary)">{c.autoReviewDescription}</span>
          </span>
        </DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup onValueChange={choose} value={active}>
          {profiles.map(profile => (
            <DropdownMenuRadioItem className={dropdownMenuRow} key={profile.name} value={profile.name}>
              <span className="min-w-0 flex-1 truncate">{profile.name}</span>
              <span className="text-muted-foreground shrink-0 text-[0.6875rem]">{profile.preset}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <p className="text-muted-foreground px-2.5 pb-2 pt-1 text-[0.6875rem]">{c.sandboxProfileHint}</p>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
