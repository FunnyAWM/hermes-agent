import { displayProviders, useOwnerCatalog } from '@/app/settings/delegation-model-provider-field'
import { withActive } from '@/app/settings/model-select'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useI18n } from '@/i18n'
import { findCatalogProvider } from '@/lib/model-options'
interface CatalogSelectProps {
  id: string
  label: string
  isProvider: boolean
  value: string
  provider: string
  scope?: string | null
  disabled: boolean
  onChange: (value: string) => void
}

export function PluginCatalogSelect({
  id,
  label,
  isProvider,
  value,
  provider,
  scope,
  disabled,
  onChange
}: CatalogSelectProps) {
  const { t } = useI18n()
  const { providers, query } = useOwnerCatalog(scope)

  const choices = isProvider
    ? displayProviders(providers, value).map(p => ({ id: p.slug, name: p.name || p.slug }))
    : withActive(findCatalogProvider(providers, provider || query.data?.provider || '')?.models ?? [], value).map(
        id => ({ id, name: id })
      )

  return (
    <Select
      disabled={disabled}
      onValueChange={item => onChange(item.slice('option:'.length))}
      value={`option:${value}`}
    >
      <SelectTrigger aria-label={label} id={id}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="option:">{t.settings.model.subagents.routing.profileDefault}</SelectItem>
        {choices.map(choice => (
          <SelectItem key={choice.id} value={`option:${choice.id}`}>
            {choice.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
