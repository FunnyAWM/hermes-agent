import type { ModelOptionsResult } from '@hermes/shared'
import { useStore } from '@nanostores/react'
import { useQuery } from '@tanstack/react-query'
import type * as React from 'react'
import { useCallback, useMemo, useRef } from 'react'

import { saveHermesConfig } from '@/api/config'
import { type HermesGateway } from '@/hermes'
import { quickModelOptions } from '@/lib/chat-runtime'
import { currentModelCapabilities, modelOptionsQueryKey, requestModelOptions } from '@/lib/model-options'
import { requestGatewayForActiveRoute } from '@/store/active-route-request'
import {
  $approvalModes,
  type ApprovalMode,
  type ApprovalModeRequester,
  setApprovalModeForProfile
} from '@/store/approval-mode'

import { hermesConfigCacheWriter, useHermesConfigRecord } from '../hooks/use-config-record'
import { getNested, setConfigField } from '../settings/helpers'

import type { ChatBarState } from './composer/types'

// Shipped profile names the backend always knows (tools/environments/local_command_sandbox.py);
// used until the config record arrives or when it carries no profiles map.
const SHIPPED_SANDBOX_PROFILES = ['read-only', 'workspace-write', 'workspace-exec', 'full-access']
const DEFAULT_SANDBOX_PROFILE = 'workspace-exec'

interface ChatBarStateOptions {
  activeGatewayProfile: string
  activeSessionId: null | string
  contextSuggestions: ChatBarState['tools']['suggestions']
  currentModel: string
  currentProvider: string
  gateway: HermesGateway | null
  gatewayOpen: boolean
  modelMenuContent?: React.ReactNode
  modelOptionsOwnerConnectionId?: string
  modelOptionsProfile?: string
  reasoningMenuContent?: React.ReactNode
  requestModelOptionsForOwner?: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
}

// The composer's model / tools / voice state, backed by the model-options query
// for the active session's profile.
export function useChatBarState({
  activeGatewayProfile,
  activeSessionId,
  contextSuggestions,
  currentModel,
  currentProvider,
  gateway,
  gatewayOpen,
  modelMenuContent,
  modelOptionsOwnerConnectionId,
  modelOptionsProfile,
  reasoningMenuContent,
  requestModelOptionsForOwner
}: ChatBarStateOptions): ChatBarState {
  const modelOptionsQuery = useQuery<ModelOptionsResult>({
    queryKey: modelOptionsQueryKey(
      modelOptionsProfile || activeGatewayProfile,
      activeSessionId,
      modelOptionsOwnerConnectionId
    ),
    queryFn: () =>
      requestModelOptions({
        gateway: gateway || undefined,
        profile: modelOptionsProfile || activeGatewayProfile,
        request: requestModelOptionsForOwner,
        sessionId: activeSessionId
      }),
    enabled: gatewayOpen
  })

  const quickModels = useMemo(
    () => quickModelOptions(modelOptionsQuery.data, currentProvider, currentModel),
    [currentModel, currentProvider, modelOptionsQuery.data]
  )

  const caps = currentModelCapabilities(modelOptionsQuery.data, currentProvider, currentModel)
  const supportsReasoning = caps?.reasoning
  const supportsFast = caps ? Boolean(caps.fast) : undefined
  const supportsUltrafast = caps ? Boolean(caps.ultrafast) : undefined

  // Same server record the settings panel edits; the pick writes one sparse key, then
  // mirrors it into the shared cache so every surface agrees without a refetch.
  // A tile pinned to another profile scopes the read/write; a tile on the ambient
  // profile omits it so both sides use the same cache row as every other surface
  // (the gate reads config.yaml for the session's home, so a pin must reach that home).
  const configProfile =
    modelOptionsProfile && modelOptionsProfile !== activeGatewayProfile ? modelOptionsProfile : undefined

  const { data: configRecord } = useHermesConfigRecord(configProfile)

  // Auto Review is an approval mode, independent of the sandbox profile: the
  // pill only toggles it, and remembers the mode it replaced so switching back
  // restores what the user ran before (this profile only — modes are per profile).
  const approvalProfile = modelOptionsProfile || activeGatewayProfile
  const approvalModes = useStore($approvalModes)
  const approvalMode: ApprovalMode = approvalModes[approvalProfile.trim() || 'default'] ?? 'smart'
  const previousApprovalModes = useRef<Record<string, ApprovalMode>>({})

  const requestApprovalMode = useCallback<ApprovalModeRequester>(
    (method, params) => requestGatewayForActiveRoute(approvalProfile, method, params),
    [approvalProfile]
  )

  const toggleAutoReview = useCallback(
    async (enabled: boolean) => {
      const key = approvalProfile.trim() || 'default'

      if (enabled) {
        if (approvalMode !== 'auto') {
          previousApprovalModes.current[key] = approvalMode
        }

        await setApprovalModeForProfile(requestApprovalMode, approvalProfile, 'auto')

        return
      }

      await setApprovalModeForProfile(
        requestApprovalMode,
        approvalProfile,
        previousApprovalModes.current[key] ?? 'smart'
      )
    },
    [approvalMode, approvalProfile, requestApprovalMode]
  )

  const sandbox = useMemo<ChatBarState['sandbox']>(() => {
    if (!configRecord || getNested(configRecord, 'terminal.sandbox.enabled') === false) {
      return undefined
    }

    const rawProfiles = getNested(configRecord, 'terminal.sandbox.profiles')

    const entries =
      rawProfiles && typeof rawProfiles === 'object' && !Array.isArray(rawProfiles) && Object.keys(rawProfiles).length
        ? Object.entries(rawProfiles as Record<string, { preset?: unknown }>).map(([name, entry]) => ({
            name,
            preset: String(entry?.preset ?? name)
          }))
        : SHIPPED_SANDBOX_PROFILES.map(name => ({ name, preset: name }))

    const active = String(getNested(configRecord, 'terminal.sandbox.profile') ?? DEFAULT_SANDBOX_PROFILE)
    // Same scope string the hook read with, so the write lands on the record this
    // surface renders (a read-origin object would key a different cache row).
    const writeCache = hermesConfigCacheWriter(configProfile)

    return {
      active,
      approvalMode,
      profiles: entries,
      onToggleAutoReview: toggleAutoReview,
      onPick: async (name: string) => {
        const patch = setConfigField({}, 'terminal.sandbox.profile', name)
        const result = await saveHermesConfig(patch, configProfile)

        if (!result.ok) {
          throw new Error('sandbox profile save failed')
        }

        writeCache(previous => (previous ? setConfigField(previous, 'terminal.sandbox.profile', name) : patch))
      }
    }
  }, [approvalMode, configProfile, configRecord, toggleAutoReview])

  return useMemo<ChatBarState>(
    () => ({
      model: {
        model: currentModel,
        provider: currentProvider,
        canSwitch: gatewayOpen,
        loading: !gatewayOpen || (!currentModel && !currentProvider),
        modelMenuContent,
        quickModels,
        reasoningMenuContent,
        supportsFast,
        supportsReasoning,
        supportsUltrafast
      },
      sandbox,
      tools: {
        enabled: true,
        label: 'Add context',
        suggestions: contextSuggestions
      },
      voice: {
        enabled: true,
        active: false
      }
    }),
    [
      contextSuggestions,
      currentModel,
      currentProvider,
      gatewayOpen,
      modelMenuContent,
      quickModels,
      reasoningMenuContent,
      sandbox,
      supportsFast,
      supportsReasoning,
      supportsUltrafast
    ]
  )
}
