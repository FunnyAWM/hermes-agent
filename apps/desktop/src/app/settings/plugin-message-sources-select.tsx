import { useStore } from '@nanostores/react'
import { useQuery } from '@tanstack/react-query'

import { $apiRequestScope } from '@/api/client'
import { getMessagingPlatforms } from '@/api/messaging'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuSeparator,
  DropdownMenuTrigger
} from '@/components/ui/dropdown-menu'
import { useI18n } from '@/i18n'
import { messageSourceLabels } from '@/i18n/agent-plugin-settings'
import type { PluginSettingField } from '@/store/agent-plugins'

interface MessageSourcesSelectProps {
  id: string
  label: string
  scope?: string | null
  write: boolean
  disabled: boolean
  raw: string
  onChange: (raw: string) => void
}

// Relay transports messages on behalf of their logical IM. The API and webhook are HTTP listeners.
const nonChatPlatforms = new Set(['api_server', 'webhook', 'relay'])
const sourceFields = new Set(['message_sources', 'write_message_sources'])

export function isMessageSourcesField(plugin: string | undefined, field: PluginSettingField): boolean {
  return plugin === 'super_agent' && field.type === 'json' && sourceFields.has(field.key)
}

export function PluginMessageSourcesSelect({
  id,
  label,
  scope,
  write,
  disabled,
  raw,
  onChange
}: MessageSourcesSelectProps) {
  const { locale } = useI18n()
  const copy = messageSourceLabels[locale.startsWith('zh') ? 'zh' : 'en']
  const ambient = useStore($apiRequestScope)
  const profile = scope === undefined ? ambient.profile : scope

  const { data } = useQuery({
    queryKey: ['plugin-message-sources', ambient.connectionId, profile],
    queryFn: () => getMessagingPlatforms({ connectionId: ambient.connectionId, profile }),
    retry: false
  })

  // The form serializes the JSON field when it seeds or updates the draft. Keep legacy IDs
  // selectable even if their platform is offline, removed, or missing from this response.
  const stored: unknown = raw ? JSON.parse(raw) : []
  const selected: string[] = Array.isArray(stored) ? stored.filter((v): v is string => typeof v === 'string') : []
  const choices = new Map<string, string>([['*', copy.all]])

  if (write) {
    choices.set('same-as-read', copy.same)
  }

  choices.set('local', copy.local)

  for (const row of data?.platforms ?? []) {
    if (row.configured && !nonChatPlatforms.has(row.id) && row.id !== 'same-as-read' && !choices.has(row.id)) {
      choices.set(row.id, row.name || row.id)
    }
  }

  for (const value of selected) {
    if (!choices.has(value)) {
      choices.set(value, value)
    }
  }

  const exclusive = (value: string) => value === '*' || (write && value === 'same-as-read')

  const toggle = (value: string) => {
    const next = selected.includes(value)
      ? selected.filter(id => id !== value)
      : exclusive(value)
        ? [value]
        : [...selected.filter(id => !exclusive(id)), value]

    onChange(JSON.stringify(next, null, 2))
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={label}
          className="max-w-full gap-2"
          disabled={disabled}
          id={id}
          type="button"
          variant="outline"
        >
          <span className="truncate">{selected.map(id => choices.get(id)).join(', ') || copy.none}</span>
          <Codicon name="chevron-down" size="0.75rem" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" aria-label={label}>
        <DropdownMenuCheckboxItem
          checked={selected.length === 0}
          onSelect={event => {
            event.preventDefault()
            onChange('[]')
          }}
        >
          {copy.none}
        </DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        {[...choices].map(([value, name]) => (
          <DropdownMenuCheckboxItem
            checked={selected.includes(value)}
            key={value}
            onSelect={event => {
              event.preventDefault()
              toggle(value)
            }}
          >
            {name}
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
