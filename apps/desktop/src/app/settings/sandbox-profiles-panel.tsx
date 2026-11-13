import { type ReactNode, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/i18n'
import { Plus, ShieldLock, Trash2 } from '@/lib/icons'
import { confirm } from '@/store/confirm'
import type { HermesConfigRecord } from '@/types/hermes'

import { CONTROL_TEXT, EMPTY_SELECT_VALUE } from './constants'
import { getNested, setConfigField } from './helpers'
import { ListRow, Pill, SectionHeading, ToggleRow } from './primitives'

// Presets the backend understands (tools/environments/local_command_sandbox.py).
// Each is a superset of the one before it; every preset denies network.
const SANDBOX_PRESET_IDS = ['read-only', 'workspace-write', 'workspace-exec', 'full-access', 'custom'] as const
type PresetId = (typeof SANDBOX_PRESET_IDS)[number]

const PRESET_COPY_KEY = {
  'read-only': 'readOnly',
  'workspace-write': 'workspaceWrite',
  'workspace-exec': 'workspaceExec',
  'full-access': 'fullAccess',
  custom: 'custom'
} as const

type SandboxCopy = ReturnType<typeof useI18n>['t']['settings']['sandboxProfiles']

const presetCopy = (copy: SandboxCopy, preset: string) =>
  copy.presets[PRESET_COPY_KEY[preset as PresetId] ?? 'workspaceWrite']

const presetsFor = (copy: SandboxCopy) =>
  SANDBOX_PRESET_IDS.map(id => ({ id, title: presetCopy(copy, id).title }))

/** Full Access is the one level that leaves the machine open: ask before creating or activating it. */
async function consentToFullAccess(preset: string | undefined, copy: SandboxCopy): Promise<boolean> {
  if (preset !== 'full-access') {
    return true
  }

  return confirm({ destructive: true, ...copy.fullAccess })
}

// The sandctl `spec` fragment a custom profile may add on top of its preset. Lists
// union with the generated grants, so an override can only add paths.
interface PolicyOverlay {
  fs?: { mode?: string }
  net?: { mode?: string; default?: string; allow?: string[]; allowCidr?: string[]; deny?: string[] }
  syscall?: { profile?: string }
  proc?: { cpu?: string; mem?: string; pids?: number; wallTime?: string; noNewPrivs?: boolean }
  secrets?: { inject?: boolean; proxy?: boolean; allow?: string[] }
}

interface ProfileEntry {
  preset: PresetId
  read: string[]
  write: string[]
  backend: string
  policy?: PolicyOverlay
}

const DEFAULT_PROFILE = 'workspace-exec'

const asStringList = (value: unknown): string[] =>
  Array.isArray(value) ? value.map(item => String(item)).filter(Boolean) : []

const hasValue = (value: unknown) =>
  value !== undefined && value !== null && value !== '' && !(Array.isArray(value) && value.length === 0)

const pruneEmpty = (node: Record<string, unknown>): boolean => {
  for (const [key, value] of Object.entries(node)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      if (pruneEmpty(value as Record<string, unknown>)) {
        delete node[key]
      }
    } else if (!hasValue(value)) {
      delete node[key]
    }
  }

  return Object.keys(node).length === 0
}

/** Set or clear one dotted path inside the overlay, keeping every key the user did not touch. */
function withPolicyValue(
  policy: PolicyOverlay | undefined,
  path: string[],
  value: unknown
): PolicyOverlay | undefined {
  const next = structuredClone(policy ?? {}) as Record<string, unknown>
  let cursor: Record<string, unknown> = next

  for (const part of path.slice(0, -1)) {
    const child = cursor[part]

    if (!child || typeof child !== 'object' || Array.isArray(child)) {
      cursor[part] = {}
    }

    cursor = cursor[part] as Record<string, unknown>
  }

  const leaf = path[path.length - 1]

  if (hasValue(value)) {
    cursor[leaf] = value
  } else {
    delete cursor[leaf]
  }

  pruneEmpty(next)

  return Object.keys(next).length === 0 ? undefined : (next as PolicyOverlay)
}

const shippedProfiles = () =>
  Object.fromEntries(
    (['read-only', 'workspace-write', 'workspace-exec', 'full-access'] as const).map(preset => [
      preset,
      { preset, read: [], write: [], backend: '' }
    ])
  ) as Record<string, ProfileEntry>

function profilesFromConfig(config: HermesConfigRecord): Record<string, ProfileEntry> {
  const raw = getNested(config, 'terminal.sandbox.profiles')

  // A record without the section still shows the shipped presets (the backend
  // defaults to the same three); the first edit persists them explicitly.
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) || Object.keys(raw).length === 0) {
    return shippedProfiles()
  }

  return Object.fromEntries(
    Object.entries(raw as Record<string, unknown>).map(([name, value]) => {
      const entry = value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}

      return [
        name,
        {
          preset: String(entry.preset ?? DEFAULT_PROFILE) as PresetId,
          read: asStringList(entry.read),
          write: asStringList(entry.write),
          backend: String(entry.backend ?? ''),
          policy:
            entry.policy && typeof entry.policy === 'object' && !Array.isArray(entry.policy)
              ? (entry.policy as PolicyOverlay)
              : undefined
        }
      ]
    })
  )
}

function PathListEditor({
  description,
  label,
  onChange,
  placeholder,
  value
}: {
  description: string
  label: string
  onChange: (next: string[]) => void
  placeholder: string
  value: string[]
}) {
  // Local draft: rebuilding the textarea from the config on every keystroke would
  // swallow the newline the user just typed while starting a second path.
  const [draft, setDraft] = useState(value.join('\n'))

  useEffect(() => setDraft(value.join('\n')), [value])

  return (
    <label className="mt-2 block">
      <span className="text-(--ui-text-secondary)">{label}</span>
      <Textarea
        className={CONTROL_TEXT}
        onChange={event => {
          setDraft(event.target.value)
          onChange(event.target.value.split('\n').map(line => line.trim()).filter(Boolean))
        }}
        placeholder={placeholder}
        rows={2}
        value={draft}
      />
      <span className="mt-1 block text-(--ui-text-tertiary)">{description}</span>
    </label>
  )
}

function FieldRow({ children, description, label }: { children: ReactNode; description?: string; label: string }) {
  return (
    <div className="mt-2">
      <span className="text-(--ui-text-secondary)">{label}</span>
      {children}
      {description ? <span className="mt-1 block text-(--ui-text-tertiary)">{description}</span> : null}
    </div>
  )
}

function Choice({
  label,
  onChange,
  options,
  value
}: {
  label: string
  onChange: (next: string) => void
  options: readonly { id: string; title: string }[]
  value: string
}) {
  return (
    <FieldRow label={label}>
      <Select
        onValueChange={next => onChange(next === EMPTY_SELECT_VALUE ? '' : next)}
        value={value || EMPTY_SELECT_VALUE}
      >
        <SelectTrigger className="mt-1 w-full @2xl:w-72" size="sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map(option => (
            <SelectItem key={option.id || EMPTY_SELECT_VALUE} value={option.id || EMPTY_SELECT_VALUE}>
              {option.title}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </FieldRow>
  )
}

/** The sandctl knobs the presets do not model: isolation, network, syscall filter,
 *  resource caps and secret handling. Values land in `profiles.<name>.policy`, which
 *  the gate merges over the generated policy; lists only ever add. */
function PolicyEditor({ entry, onChange }: { entry: ProfileEntry; onChange: (next: ProfileEntry) => void }) {
  const { t } = useI18n()
  const copy = t.settings.sandboxProfiles
  const p = copy.policy
  const policy = entry.policy ?? {}
  const net = policy.net ?? {}
  const proc = policy.proc ?? {}
  const secrets = policy.secrets ?? {}

  const setPolicy = (path: string[], value: unknown) =>
    onChange({ ...entry, policy: withPolicyValue(entry.policy, path, value) })

  // Only ask when the change moves past the shipped boundary; tightening is free.
  const confirmed = (apply: () => void) => void confirm({ destructive: true, ...copy.widen }).then(ok => ok && apply())

  const isolationOptions = [
    { id: '', title: p.inheritGlobal },
    { id: 'l1', title: p.l1 },
    { id: 'l2', title: p.l2 },
    { id: 'l3', title: p.l3 }
  ]

  const syscallOptions = [
    { id: '', title: p.inherit },
    { id: 'none', title: p.none },
    { id: 'default', title: p.platformDefault },
    { id: 'restricted', title: p.restricted },
    { id: 'strict', title: p.strict }
  ]

  const networkModeOptions = [
    { id: '', title: p.networkModeDefault },
    { id: 'allowlist', title: p.networkModeAllowlist },
    { id: 'denylist', title: p.networkModeDenylist }
  ]

  const networkDefaultOptions = [
    { id: '', title: p.networkDefaultDefault },
    { id: 'deny', title: p.deny },
    { id: 'allow', title: p.allow }
  ]

  const fsModeOptions = [
    { id: '', title: p.fsModeDefault },
    { id: 'allowlist', title: p.allowlist },
    { id: 'denylist', title: p.denylist }
  ]

  const numberOrUndefined = (raw: string) => {
    const parsed = Number.parseInt(raw, 10)

    return Number.isNaN(parsed) ? undefined : parsed
  }

  return (
    <div className="mt-3 rounded-md border border-(--ui-border) p-2.5">
      <div className="font-medium">{p.title}</div>
      <p className="mt-0.5 text-(--ui-text-tertiary)">{p.subtitle}</p>
      <Choice
        label={p.isolation}
        onChange={backend => onChange({ ...entry, backend })}
        options={isolationOptions}
        value={entry.backend}
      />
      <Choice
        label={p.syscall}
        onChange={profile =>
          profile === 'none'
            ? confirmed(() => setPolicy(['syscall', 'profile'], profile))
            : setPolicy(['syscall', 'profile'], profile)
        }
        options={syscallOptions}
        value={policy.syscall?.profile ?? ''}
      />
      <Choice
        label={p.networkMode}
        onChange={mode =>
          mode === 'denylist'
            ? confirmed(() => setPolicy(['net', 'mode'], mode))
            : setPolicy(['net', 'mode'], mode)
        }
        options={networkModeOptions}
        value={net.mode ?? ''}
      />
      <Choice
        label={p.networkDefault}
        onChange={value =>
          value === 'allow'
            ? confirmed(() => setPolicy(['net', 'default'], value))
            : setPolicy(['net', 'default'], value)
        }
        options={networkDefaultOptions}
        value={net.default ?? ''}
      />
      <PathListEditor
        description={p.allowedHostsDesc}
        label={p.allowedHosts}
        onChange={allow => setPolicy(['net', 'allow'], allow)}
        placeholder={p.allowedHostsPlaceholder}
        value={net.allow ?? []}
      />
      <PathListEditor
        description={p.allowedCidrsDesc}
        label={p.allowedCidrs}
        onChange={allowCidr => setPolicy(['net', 'allowCidr'], allowCidr)}
        placeholder={p.allowedCidrsPlaceholder}
        value={net.allowCidr ?? []}
      />
      <PathListEditor
        description={p.blockedTargetsDesc}
        label={p.blockedTargets}
        onChange={deny => setPolicy(['net', 'deny'], deny)}
        placeholder={p.blockedTargetsPlaceholder}
        value={net.deny ?? []}
      />
      <Choice
        label={p.fsMode}
        onChange={mode =>
          mode === 'denylist'
            ? confirmed(() => setPolicy(['fs', 'mode'], mode))
            : setPolicy(['fs', 'mode'], mode)
        }
        options={fsModeOptions}
        value={policy.fs?.mode ?? ''}
      />
      <div className="mt-2 grid gap-2 @2xl:grid-cols-3">
        <label className="block">
          <span className="text-(--ui-text-secondary)">{p.cpu}</span>
          <Input
            className={CONTROL_TEXT}
            onChange={event => setPolicy(['proc', 'cpu'], event.target.value)}
            placeholder="2"
            value={proc.cpu ?? ''}
          />
        </label>
        <label className="block">
          <span className="text-(--ui-text-secondary)">{p.memory}</span>
          <Input
            className={CONTROL_TEXT}
            onChange={event => setPolicy(['proc', 'mem'], event.target.value)}
            placeholder="2GiB"
            value={proc.mem ?? ''}
          />
        </label>
        <label className="block">
          <span className="text-(--ui-text-secondary)">{p.pids}</span>
          <Input
            className={CONTROL_TEXT}
            inputMode="numeric"
            onChange={event => setPolicy(['proc', 'pids'], numberOrUndefined(event.target.value))}
            placeholder="512"
            type="number"
            value={proc.pids ?? ''}
          />
        </label>
      </div>
      <FieldRow label={p.wallTime}>
        <Input
          className={`${CONTROL_TEXT} mt-1 @2xl:w-72`}
          onChange={event => setPolicy(['proc', 'wallTime'], event.target.value)}
          placeholder="10m"
          value={proc.wallTime ?? ''}
        />
      </FieldRow>
      <ToggleRow
        checked={proc.noNewPrivs === true}
        description={p.noNewPrivsDesc}
        label={p.noNewPrivs}
        onChange={on => setPolicy(['proc', 'noNewPrivs'], on)}
      />
      <ToggleRow
        checked={secrets.inject === true}
        description={p.injectSecretsDesc}
        label={p.injectSecrets}
        onChange={on =>
          on ? confirmed(() => setPolicy(['secrets', 'inject'], true)) : setPolicy(['secrets', 'inject'], false)
        }
      />
      <ToggleRow
        checked={secrets.proxy === true}
        description={p.proxySecretsDesc}
        label={p.proxySecrets}
        onChange={on => setPolicy(['secrets', 'proxy'], on)}
      />
      <PathListEditor
        description={p.allowedSecretsDesc}
        label={p.allowedSecrets}
        onChange={allow => setPolicy(['secrets', 'allow'], allow)}
        placeholder={p.allowedSecretsPlaceholder}
        value={secrets.allow ?? []}
      />
    </div>
  )
}

function ProfileCard({
  canDelete,
  entry,
  name,
  onRemove,
  onUpdate
}: {
  canDelete: boolean
  entry: ProfileEntry
  name: string
  onRemove: () => void
  onUpdate: (next: ProfileEntry) => void
}) {
  const { t } = useI18n()
  const copy = t.settings.sandboxProfiles
  const current = presetCopy(copy, entry.preset)

  return (
    <div className="rounded-lg border border-(--ui-border) px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-medium">{name}</span>
        <Pill>{current.title}</Pill>
        <Button
          aria-label={`${copy.deleteProfile} ${name}`}
          disabled={!canDelete}
          onClick={onRemove}
          size="icon-sm"
          type="button"
          variant="ghost"
        >
          <Trash2 className="size-3.5" />
        </Button>
      </div>
      <Select
        onValueChange={value => {
          void consentToFullAccess(value, copy).then(ok => {
            if (ok) {
              onUpdate({ ...entry, preset: value as PresetId })
            }
          })
        }}
        value={entry.preset}
      >
        <SelectTrigger className="mt-2 w-full @2xl:w-72" size="sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {presetsFor(copy).map(preset => (
            <SelectItem key={preset.id} value={preset.id}>
              {preset.title}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <p className="mt-1.5 text-(--ui-text-tertiary)">{current.description}</p>
      {entry.preset === 'custom' ? (
        <>
          <PathListEditor
            description={copy.readDirectoriesDesc}
            label={copy.readDirectories}
            onChange={read => onUpdate({ ...entry, read })}
            placeholder={copy.pathsPlaceholder}
            value={entry.read}
          />
          <PathListEditor
            description={copy.writeDirectoriesDesc}
            label={copy.writeDirectories}
            onChange={write => onUpdate({ ...entry, write })}
            placeholder={copy.pathsPlaceholder}
            value={entry.write}
          />
          <PolicyEditor entry={entry} onChange={onUpdate} />
        </>
      ) : null}
    </div>
  )
}

/**
 * Named command-sandbox profiles (`terminal.sandbox.profiles`) and the active one.
 * The generic config editor can only show `profiles` as an opaque object, so this
 * pane is the supported way to build a boundary from the three presets.
 */
export function SandboxProfilesPanel({
  activeSectionId,
  config,
  onChange,
  subpage
}: {
  activeSectionId: string
  config: HermesConfigRecord
  onChange: (next: HermesConfigRecord) => void
  subpage: string | undefined
}) {
  const profiles = profilesFromConfig(config)
  const { t } = useI18n()
  const copy = t.settings.sandboxProfiles
  const names = Object.keys(profiles)
  const active = String(getNested(config, 'terminal.sandbox.profile') ?? DEFAULT_PROFILE)
  const [newName, setNewName] = useState('')
  const [newPreset, setNewPreset] = useState<PresetId>(DEFAULT_PROFILE)

  const writeProfiles = (next: Record<string, ProfileEntry>, activeName?: string) => {
    let updated = setConfigField(config, 'terminal.sandbox.profiles', next)

    if (activeName) {
      updated = setConfigField(updated, 'terminal.sandbox.profile', activeName)
    }

    onChange(updated)
  }

  const updateProfile = (name: string, entry: ProfileEntry) =>
    writeProfiles({ ...profiles, [name]: entry })

  const removeProfile = (name: string) => {
    if (names.length <= 1) {
      return
    }

    const next = { ...profiles }
    delete next[name]
    writeProfiles(next, active === name ? Object.keys(next)[0] : undefined)
  }

  const addProfile = () => {
    const name = newName.trim()

    if (!name || profiles[name]) {
      return
    }

    writeProfiles({ ...profiles, [name]: { preset: newPreset, read: [], write: [], backend: '' } })
    setNewName('')
  }

  if (activeSectionId !== 'workspace' || (subpage !== undefined && subpage !== 'shell')) {
    return null
  }

  return (
    <section className="mb-6">
      <SectionHeading icon={ShieldLock} title={copy.title} />
      <ListRow
        action={
          <Select
            onValueChange={name => {
              void consentToFullAccess(profiles[name]?.preset, copy).then(ok => {
                if (ok) {
                  onChange(setConfigField(config, 'terminal.sandbox.profile', name))
                }
              })
            }}
            value={active}
          >
            <SelectTrigger className="w-full @2xl:w-72" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {names.map(name => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
        description={copy.activeDesc}
        title={copy.activeTitle}
      />
      <div className="grid gap-2">
        {names.map(name => (
          <ProfileCard
            canDelete={names.length > 1}
            entry={profiles[name]}
            key={name}
            name={name}
            onRemove={() => removeProfile(name)}
            onUpdate={entry => updateProfile(name, entry)}
          />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Input
          className={`${CONTROL_TEXT} w-44`}
          onChange={event => setNewName(event.target.value)}
          placeholder={copy.profileName}
          value={newName}
        />
        <Select onValueChange={value => setNewPreset(value as PresetId)} value={newPreset}>
          <SelectTrigger className="w-44" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {presetsFor(copy).map(preset => (
              <SelectItem key={preset.id} value={preset.id}>
                {preset.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          disabled={!newName.trim() || Boolean(profiles[newName.trim()])}
          onClick={addProfile}
          size="sm"
          type="button"
          variant="outline"
        >
          <Plus className="size-3.5" />
          {copy.addProfile}
        </Button>
      </div>
    </section>
  )
}
