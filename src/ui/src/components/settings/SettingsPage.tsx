import { Loader2, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { MarkdownDocument } from '@/components/plugins/MarkdownDocument'
import { ProjectsAppBar } from '@/components/projects/ProjectsAppBar'
import { BaselineSettingsPanel } from '@/components/settings/BaselineSettingsPanel'
import { ConnectorSettingsForm } from '@/components/settings/ConnectorSettingsForm'
import { connectorCatalog, type ConnectorName } from '@/components/settings/connectorCatalog'
import { connectorConfigAutoEnabled } from '@/components/settings/connectorSettingsHelpers'
import { RegistrySettingsForm } from '@/components/settings/RegistrySettingsForm'
import { translateSettingsCatalogText, translateSettingsHelpMarkdown } from '@/components/settings/settingsCatalogI18n'
import { HintDot } from '@/components/ui/hint-dot'
import { Input } from '@/components/ui/input'
import { client } from '@/lib/api'
import { cn } from '@/lib/utils'
import type {
  BaselineRegistryEntry,
  ConfigFileEntry,
  ConfigTestPayload,
  ConfigValidationPayload,
  ConnectorSnapshot,
  Locale,
  OpenDocumentPayload,
  QuestSummary,
} from '@/types'

export type ConfigDocumentName = 'config' | 'runners' | 'connectors' | 'baselines' | 'plugins' | 'mcp_servers'

const CONFIG_ORDER: ConfigDocumentName[] = ['config', 'runners', 'connectors', 'baselines', 'plugins', 'mcp_servers']

const CONFIG_META = {
  config: {
    label: { en: 'Runtime', zh: '运行时' },
    hint: { en: 'Home paths, git, logging, and daemon defaults.', zh: '主目录路径、git、日志与 daemon 默认设置。' },
  },
  runners: {
    label: { en: 'Models', zh: '运行器' },
    hint: { en: 'Runner selection, model defaults, and execution policy.', zh: '运行器选择、模型默认值与执行策略。' },
  },
  connectors: {
    label: { en: 'Connectors', zh: '连接器' },
    hint: {
      en: 'Native connector transports, discovered runtime targets, and legacy callback fallbacks.',
      zh: '原生连接器传输方式、运行时发现目标，以及旧式回调兜底配置。',
    },
  },
  baselines: {
    label: { en: 'Baselines', zh: '基线' },
    hint: {
      en: 'Reusable baseline registry entries and lifecycle management.',
      zh: '可复用基线条目及其生命周期管理。',
    },
  },
  plugins: {
    label: { en: 'Extensions', zh: '扩展' },
    hint: { en: 'Optional plugins and local extension discovery.', zh: '可选插件与本地扩展发现。' },
  },
  mcp_servers: {
    label: { en: 'MCP', zh: 'MCP' },
    hint: { en: 'External MCP servers and access policy.', zh: '外部 MCP 服务与访问策略。' },
  },
} satisfies Record<ConfigDocumentName, { label: Record<Locale, string>; hint: Record<Locale, string> }>

const copy = {
  en: {
    title: 'Settings',
    files: 'Settings',
    search: 'Search',
    noFile: 'Pick a category.',
    saved: 'Saved.',
    noHealth: 'No connector snapshot.',
    daemon: 'Daemon',
    connectors: 'Connectors',
    enabled: 'Enabled',
    idle: 'Idle',
    dirty: 'Dirty',
    check: 'Check',
    reference: 'Notes',
    loading: 'Loading',
    qqAutoBound: 'QQ openid detected and saved automatically.',
    connectorDeleted: 'Connector profile deleted.',
    connectorBindingSaved: 'Connector binding updated.',
    baselineDeleted: 'Baseline deleted.',
  },
  zh: {
    title: '设置',
    files: '设置',
    search: '搜索',
    noFile: '选择一个分类。',
    saved: '已保存。',
    noHealth: '暂无连接器快照。',
    daemon: '守护进程',
    connectors: '连接器',
    enabled: '已启用',
    idle: '空闲',
    dirty: '未保存',
    check: '校验',
    reference: '说明',
    loading: '加载中',
    qqAutoBound: '已自动检测并保存 QQ openid。',
    connectorDeleted: '已删除该 Connector。',
    connectorBindingSaved: '已更新该 Connector 绑定。',
    baselineDeleted: '已删除该 baseline。',
  },
} satisfies Record<Locale, Record<string, string>>

const SYNTHETIC_BASELINE_FILE: ConfigFileEntry = {
  name: 'baselines',
  path: 'config/baselines',
  required: false,
  exists: true,
}

function compareConfig(a: ConfigFileEntry, b: ConfigFileEntry) {
  return CONFIG_ORDER.indexOf(a.name as ConfigDocumentName) - CONFIG_ORDER.indexOf(b.name as ConfigDocumentName)
}

function configLabel(name: ConfigDocumentName, locale: Locale) {
  return CONFIG_META[name].label[locale]
}

function connectorBindingTransitionMessage(transition: unknown, questId?: string | null, locale: Locale = 'en') {
  if (!transition || typeof transition !== 'object') {
    return locale === 'zh' ? copy.zh.connectorBindingSaved : copy.en.connectorBindingSaved
  }
  const payload = transition as Record<string, unknown>
  const mode = String(payload.mode || '').trim().toLowerCase()
  const previousLabel = String(payload.previous_label || '').trim()
  const currentLabel = String(payload.current_label || '').trim()
  const resolvedQuestId = String(questId || payload.quest_id || '').trim()
  if (locale === 'zh') {
    if (mode === 'switch' && previousLabel && currentLabel && resolvedQuestId) {
      return `已将 ${resolvedQuestId} 从 ${previousLabel} 切换到 ${currentLabel}。`
    }
    if (mode === 'bind' && currentLabel && resolvedQuestId) {
      return `已将 ${resolvedQuestId} 绑定到 ${currentLabel}。`
    }
    if (mode === 'disconnect' && resolvedQuestId) {
      return `${resolvedQuestId} 已切换为仅本地。`
    }
    return copy.zh.connectorBindingSaved
  }
  if (mode === 'switch' && previousLabel && currentLabel && resolvedQuestId) {
    return `Switched ${resolvedQuestId} from ${previousLabel} to ${currentLabel}.`
  }
  if (mode === 'bind' && currentLabel && resolvedQuestId) {
    return `Bound ${resolvedQuestId} to ${currentLabel}.`
  }
  if (mode === 'disconnect' && resolvedQuestId) {
    return `${resolvedQuestId} is now local only.`
  }
  return copy.en.connectorBindingSaved
}

function configHint(name: ConfigDocumentName, locale: Locale) {
  return CONFIG_META[name].hint[locale]
}

function normalizeHashAnchor(value?: string | null) {
  return String(value || '')
    .trim()
    .replace(/^#/, '')
}

function settingsConfigPath(name: ConfigDocumentName | null, connectorName?: ConnectorName | null) {
  if (name === 'connectors') {
    return connectorName ? `/settings/connector/${connectorName}` : '/settings/connector'
  }
  return name ? `/settings/${name}` : '/settings'
}

function connectorNameFromAnchor(anchorId: string): ConnectorName | null {
  const normalized = normalizeHashAnchor(anchorId)
  const match = /^connector-(qq|weixin|telegram|discord|slack|feishu|whatsapp|lingzhu)(?:$|-)/.exec(normalized)
  return (match?.[1] as ConnectorName | undefined) ?? null
}

function scrollSettingsAnchor(root: HTMLElement | null, anchorId: string) {
  if (!root || !anchorId) {
    return false
  }
  const target = root.querySelector<HTMLElement>(`#${CSS.escape(anchorId)}`)
  if (!target) {
    return false
  }
  const top =
    root.scrollTop +
    target.getBoundingClientRect().top -
    root.getBoundingClientRect().top -
    16
  root.scrollTo({ top: Math.max(top, 0), behavior: 'smooth' })
  return true
}

function qqMainChatSignature(value: unknown) {
  if (!value || typeof value !== 'object') {
    return ''
  }
  const payload = value as Record<string, unknown>
  const directMainChatId = String(payload.main_chat_id || '').trim()
  const profileChatIds = Array.isArray(payload.profiles)
    ? payload.profiles
        .filter((item) => item && typeof item === 'object')
        .map((item) => {
          const profile = item as Record<string, unknown>
          return `${String(profile.profile_id || '').trim()}:${String(profile.main_chat_id || '').trim()}`
        })
        .filter((item) => item !== ':')
        .sort()
    : []
  return [directMainChatId, ...profileChatIds].filter(Boolean).join('|')
}

export function SettingsPage({
  requestedConfigName,
  requestedConnectorName,
  onRequestedConfigConsumed,
  runtimeAddress,
  locale,
}: {
  requestedConfigName?: ConfigDocumentName | null
  requestedConnectorName?: ConnectorName | null
  onRequestedConfigConsumed?: () => void
  runtimeAddress: string
  locale: Locale
}) {
  const t = copy[locale]
  const location = useLocation()
  const navigate = useNavigate()
  const [files, setFiles] = useState<ConfigFileEntry[]>([])
  const [connectors, setConnectors] = useState<ConnectorSnapshot[]>([])
  const [baselineEntries, setBaselineEntries] = useState<BaselineRegistryEntry[]>([])
  const [quests, setQuests] = useState<QuestSummary[]>([])
  const [selectedName, setSelectedName] = useState<ConfigDocumentName | null>(requestedConfigName || null)
  const [document, setDocument] = useState<OpenDocumentPayload | null>(null)
  const [structuredDraft, setStructuredDraft] = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(true)
  const [documentLoading, setDocumentLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validating, setValidating] = useState(false)
  const [testingAll, setTestingAll] = useState(false)
  const [deletingProfileKey, setDeletingProfileKey] = useState('')
  const [deletingBaselineId, setDeletingBaselineId] = useState('')
  const [bindingProfileKey, setBindingProfileKey] = useState('')
  const [validation, setValidation] = useState<ConfigValidationPayload | null>(null)
  const [testResult, setTestResult] = useState<ConfigTestPayload | null>(null)
  const [saveMessage, setSaveMessage] = useState('')
  const [search, setSearch] = useState('')
  const lastKnownQqMainChatIdRef = useRef('')
  const contentRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    let mounted = true
    const load = async () => {
      setLoading(true)
      try {
        const [filePayload, connectorPayload, baselinePayload, questPayload] = await Promise.all([
          client.configFiles(),
          client.connectors(),
          client.baselines(),
          client.quests(),
        ])
        if (!mounted) {
          return
        }
        const sorted = [...filePayload].sort(compareConfig)
        setFiles([...sorted, SYNTHETIC_BASELINE_FILE].sort(compareConfig))
        setConnectors(connectorPayload)
        setBaselineEntries(baselinePayload)
        setQuests(questPayload)
        const preferred = requestedConfigName || (sorted[0]?.name as ConfigDocumentName | undefined) || null
        if (preferred) {
          setSelectedName(preferred)
        }
      } finally {
        if (mounted) {
          setLoading(false)
        }
      }
    }
    void load()
    return () => {
      mounted = false
    }
  }, [requestedConfigName])

  useEffect(() => {
    if (!selectedName) {
      setDocument(null)
      setStructuredDraft({})
      setDocumentLoading(false)
      return
    }
    if (selectedName === 'baselines') {
      setDocument(null)
      setStructuredDraft({})
      setValidation(null)
      setTestResult(null)
      setSaveMessage('')
      setDocumentLoading(false)
      return
    }
    let mounted = true
    setDocumentLoading(true)
    setDocument(null)
    setStructuredDraft({})
    setValidation(null)
    setTestResult(null)
    setSaveMessage('')
    const load = async () => {
      try {
        const next = await client.configDocument(selectedName)
        if (!mounted) {
          return
        }
        setDocument(next)
        setStructuredDraft(
          next.meta?.structured_config && typeof next.meta.structured_config === 'object'
            ? (next.meta.structured_config as Record<string, unknown>)
            : {}
        )
      } finally {
        if (mounted) {
          setDocumentLoading(false)
        }
      }
    }
    void load()
    return () => {
      mounted = false
    }
  }, [selectedName])

  useEffect(() => {
    if (!requestedConfigName) {
      return
    }
    setSelectedName(requestedConfigName)
    onRequestedConfigConsumed?.()
  }, [onRequestedConfigConsumed, requestedConfigName])

  const isPageLoading = loading || documentLoading
  const isConnectorDocument = selectedName === 'connectors'
  const isBaselineDocument = selectedName === 'baselines'
  const visibleConnectorNames = useMemo(
    () => new Set(connectors.filter((item) => item.name !== 'local').map((item) => item.name as ConnectorName)),
    [connectors]
  )
  const visibleConnectorEntries = useMemo(
    () => connectorCatalog.filter((entry) => visibleConnectorNames.has(entry.name)),
    [visibleConnectorNames]
  )
  const selectedConnectorName =
    isConnectorDocument && requestedConnectorName && visibleConnectorNames.has(requestedConnectorName)
      ? requestedConnectorName
      : null
  const isDirty = Boolean(
    document && JSON.stringify(document.meta?.structured_config || {}) !== JSON.stringify(structuredDraft)
  )
  const selectedAnchorId = selectedName ? `settings-${selectedName}` : ''

  useEffect(() => {
    if (selectedName !== 'connectors' || selectedConnectorName !== 'qq') {
      lastKnownQqMainChatIdRef.current = ''
      return
    }

    let cancelled = false
    const poll = async () => {
      try {
        const connectorPayload = await client.connectors()
        if (cancelled) {
          return
        }
        setConnectors(connectorPayload)

        if (isDirty) {
          return
        }

        const next = await client.configDocument('connectors')
        if (cancelled) {
          return
        }
        const nextStructured =
          next.meta?.structured_config && typeof next.meta.structured_config === 'object'
            ? (next.meta.structured_config as Record<string, unknown>)
            : {}
        const nextQq =
          nextStructured.qq && typeof nextStructured.qq === 'object'
            ? (nextStructured.qq as Record<string, unknown>)
            : {}
        const nextMainChatSignature = qqMainChatSignature(nextQq)

        if (next.revision !== document?.revision || nextMainChatSignature !== lastKnownQqMainChatIdRef.current) {
          setDocument(next)
          setStructuredDraft(nextStructured)
          if (!lastKnownQqMainChatIdRef.current && nextMainChatSignature) {
            setSaveMessage(t.qqAutoBound)
          }
          lastKnownQqMainChatIdRef.current = nextMainChatSignature
        }
      } catch {
        return
      }
    }

    const currentQq =
      structuredDraft.qq && typeof structuredDraft.qq === 'object'
        ? (structuredDraft.qq as Record<string, unknown>)
        : {}
    lastKnownQqMainChatIdRef.current = qqMainChatSignature(currentQq)

    void poll()
    const timer = window.setInterval(() => {
      void poll()
    }, 10000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [document?.revision, isDirty, selectedConnectorName, selectedName, structuredDraft.qq, t.qqAutoBound])

  useEffect(() => {
    const anchorId = normalizeHashAnchor(location.hash)
    if (!anchorId || isPageLoading || !selectedName) {
      return
    }
    let cancelled = false
    const attemptScroll = (attempt: number) => {
      if (cancelled) {
        return
      }
      if (scrollSettingsAnchor(contentRef.current, anchorId)) {
        return
      }
      if (attempt < 12) {
        window.setTimeout(() => attemptScroll(attempt + 1), 80)
      }
    }
    window.setTimeout(() => attemptScroll(0), 0)
    return () => {
      cancelled = true
    }
  }, [document?.revision, isPageLoading, location.hash, selectedName])

  useEffect(() => {
    if (loading) {
      return
    }
    if (!isConnectorDocument || !requestedConnectorName) {
      return
    }
    if (visibleConnectorNames.has(requestedConnectorName)) {
      return
    }
    navigate(
      {
        pathname: settingsConfigPath('connectors'),
        hash: '',
      },
      { replace: true }
    )
  }, [isConnectorDocument, loading, navigate, requestedConnectorName, visibleConnectorNames])

  useEffect(() => {
    if (!isConnectorDocument || selectedConnectorName) {
      return
    }
    const anchorConnectorName = connectorNameFromAnchor(location.hash)
    if (!anchorConnectorName) {
      return
    }
    if (!visibleConnectorNames.has(anchorConnectorName)) {
      navigate(
        {
          pathname: settingsConfigPath('connectors'),
          hash: '',
        },
        { replace: true }
      )
      return
    }
    navigate(
      {
        pathname: settingsConfigPath('connectors', anchorConnectorName),
        hash: location.hash,
      },
      { replace: true }
    )
  }, [isConnectorDocument, location.hash, navigate, selectedConnectorName, visibleConnectorNames])

  const handleSelectName = (name: ConfigDocumentName) => {
    setSelectedName(name)
    setSaveMessage('')
    navigate(
      {
        pathname: settingsConfigPath(name),
        hash: '',
      },
      { replace: location.pathname === settingsConfigPath(name) }
    )
  }

  const filteredFiles = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) {
      return files
    }
    return files.filter((item) => {
      const name = item.name as ConfigDocumentName
      return `${item.name} ${item.path} ${configLabel(name, locale)} ${configHint(name, locale)}`
        .toLowerCase()
        .includes(keyword)
    })
  }, [files, locale, search])

  const selectedMeta = selectedName ? CONFIG_META[selectedName] : null
  const helpMarkdown = translateSettingsHelpMarkdown(
    locale,
    typeof document?.meta?.help_markdown === 'string' ? document.meta.help_markdown : ''
  )

  const structuredConnectors = useMemo(
    () => (isConnectorDocument ? (structuredDraft as Record<string, Record<string, unknown>>) : {}),
    [isConnectorDocument, structuredDraft]
  )
  const setStructuredConnectors = (next: Record<string, Record<string, unknown>>) => {
    setStructuredDraft(next as Record<string, unknown>)
  }

  const jumpToAnchor = (anchorId: string) => {
    if (!selectedName || !anchorId) {
      return
    }
    const nextConnectorName =
      selectedName === 'connectors'
        ? selectedConnectorName || connectorNameFromAnchor(anchorId)
        : null
    navigate(
      {
        pathname: settingsConfigPath(selectedName, nextConnectorName),
        hash: `#${anchorId}`,
      },
      { replace: false }
    )
    window.setTimeout(() => {
      scrollSettingsAnchor(contentRef.current, anchorId)
    }, 0)
  }

  const connectorSummary = useMemo(() => {
    const snapshotByName = new Map(connectors.map((item) => [item.name, item]))
    return visibleConnectorEntries.map((entry) => {
      const configured = structuredDraft[entry.name]
      const configEnabled =
        configured && typeof configured === 'object'
          ? connectorConfigAutoEnabled(entry.name, configured as Record<string, unknown>)
          : false
      const snapshot = snapshotByName.get(entry.name)
      return {
        name: entry.name,
        label: translateSettingsCatalogText(locale, entry.label),
        enabled: selectedName === 'connectors' ? configEnabled : Boolean(snapshot?.enabled),
      }
    })
  }, [connectors, locale, selectedName, structuredDraft, visibleConnectorEntries])

  const refreshSelected = async () => {
    if (!selectedName) {
      return
    }
    if (selectedName === 'baselines') {
      setBaselineEntries(await client.baselines())
      return
    }
    const next = await client.configDocument(selectedName)
    setDocument(next)
    setStructuredDraft(
      next.meta?.structured_config && typeof next.meta.structured_config === 'object'
        ? (next.meta.structured_config as Record<string, unknown>)
        : {}
    )
  }

  const runValidate = async () => {
    if (!selectedName) {
      return
    }
    setValidating(true)
    try {
      setValidation(await client.validateConfig(selectedName, { structured: structuredDraft }))
    } finally {
      setValidating(false)
    }
  }

  const runTestAll = async () => {
    if (!selectedName) {
      return
    }
    setTestingAll(true)
    try {
      setTestResult(await client.testConfig(selectedName, { structured: structuredDraft, live: true }))
    } finally {
      setTestingAll(false)
    }
  }

  const handleSave = async (draftOverride?: Record<string, unknown>) => {
    if (!selectedName || !document) {
      return false
    }
    setSaving(true)
    try {
      const effectiveDraft = draftOverride ?? structuredDraft
      const nextStructuredDraft =
        selectedName === 'config'
          ? {
              ...effectiveDraft,
              bootstrap: {
                ...(effectiveDraft.bootstrap && typeof effectiveDraft.bootstrap === 'object'
                  ? (effectiveDraft.bootstrap as Record<string, unknown>)
                  : {}),
                locale_source: 'user',
              },
            }
          : effectiveDraft
      const result = await client.saveConfig(
        selectedName,
        { structured: nextStructuredDraft, revision: document.revision }
      )
      if (result.ok) {
        setSaveMessage(t.saved)
        await refreshSelected()
        setConnectors(await client.connectors())
        return true
      } else {
        setSaveMessage('')
        return false
      }
    } finally {
      setSaving(false)
    }
  }

  const refreshConnectorSettings = async () => {
    await refreshSelected()
    setConnectors(await client.connectors())
  }

  const handleDeleteConnectorProfile = async (connectorName: ConnectorName, profileId: string) => {
    setDeletingProfileKey(`${connectorName}:${profileId}`)
    try {
      await client.deleteConnectorProfile(connectorName, profileId)
      setSaveMessage(t.connectorDeleted)
      await refreshSelected()
      const [connectorPayload, questPayload] = await Promise.all([client.connectors(), client.quests()])
      setConnectors(connectorPayload)
      setQuests(questPayload)
    } catch (error) {
      setSaveMessage(error instanceof Error ? error.message : String(error || 'Failed to delete connector profile.'))
    } finally {
      setDeletingProfileKey('')
    }
  }

  const handleDeleteBaseline = async (baselineId: string) => {
    setDeletingBaselineId(baselineId)
    try {
      await client.deleteBaseline(baselineId)
      setSaveMessage(t.baselineDeleted)
      const [baselinePayload, questPayload] = await Promise.all([client.baselines(), client.quests()])
      setBaselineEntries(baselinePayload)
      setQuests(questPayload)
    } catch (error) {
      setSaveMessage(error instanceof Error ? error.message : String(error || 'Failed to delete baseline.'))
      throw error
    } finally {
      setDeletingBaselineId('')
    }
  }

  const handleManageConnectorBinding = async ({
    connectorName,
    profileId,
    conversationId,
    currentQuestId,
    nextQuestId,
  }: {
    connectorName: ConnectorName
    profileId: string
    conversationId: string
    currentQuestId?: string | null
    nextQuestId?: string | null
  }) => {
    const actionKey = `${connectorName}:${profileId}`
    setBindingProfileKey(actionKey)
    try {
      const normalizedNextQuestId = String(nextQuestId || '').trim() || null
      const normalizedCurrentQuestId = String(currentQuestId || '').trim() || null
      if (!normalizedNextQuestId && !normalizedCurrentQuestId) {
        return
      }
      let result: Record<string, unknown>
      if (normalizedNextQuestId) {
        result = await client.updateQuestBindings(normalizedNextQuestId, {
          connector: connectorName,
          conversation_id: conversationId,
          force: true,
        })
      } else {
        result = await client.updateQuestBindings(normalizedCurrentQuestId as string, {
          connector: connectorName,
          conversation_id: null,
          force: true,
        })
      }
      if (!result.ok) {
        throw new Error(String(result.message || 'Unable to update connector binding.'))
      }
      setSaveMessage(
        connectorBindingTransitionMessage(
          (result as Record<string, unknown>).binding_transition,
          normalizedNextQuestId || normalizedCurrentQuestId,
          locale
        )
      )
      const [connectorPayload, questPayload] = await Promise.all([client.connectors(), client.quests()])
      setConnectors(connectorPayload)
      setQuests(questPayload)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error || 'Unable to update connector binding.')
      setSaveMessage(message)
      throw error
    } finally {
      setBindingProfileKey('')
    }
  }

  return (
    <div className="font-project flex h-screen flex-col overflow-hidden px-4 pb-4 pt-4 sm:px-6 sm:pb-6">
      <ProjectsAppBar title={t.title} />

      <main className="mx-auto mt-6 min-h-0 w-full flex-1 overflow-hidden">
        <div className="mx-auto grid h-full min-h-0 w-full max-w-[90vw] grid-rows-[auto_minmax(0,1fr)] gap-0 xl:grid-cols-[250px_minmax(0,1fr)] xl:grid-rows-1">
          <aside className="feed-scrollbar flex min-h-0 flex-col overflow-auto border-b border-black/[0.08] pb-6 xl:border-b-0 xl:border-r xl:pb-0 xl:pr-6 dark:border-white/[0.08]">
            <div className="text-sm font-medium">{t.files}</div>

            <div className="relative mt-4">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t.search} className="rounded-[18px] border-black/[0.08] bg-white/[0.44] pl-10 shadow-none dark:bg-white/[0.03]" />
            </div>

            <div className="mt-5">
              {filteredFiles.map((file, index) => {
                const name = file.name as ConfigDocumentName
                return (
                  <button
                    key={file.name}
                    type="button"
                    onClick={() => handleSelectName(name)}
                    className={cn(
                      'flex w-full items-center justify-between border-t border-black/[0.06] py-3 text-left transition first:border-t-0 dark:border-white/[0.08]',
                      selectedName === file.name ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'
                    )}
                    style={{ marginTop: index === 0 ? 0 : undefined }}
                  >
                    <span className="flex items-center gap-2">
                      <span className="text-sm font-medium">{configLabel(name, locale)}</span>
                      <HintDot label={configHint(name, locale)} />
                    </span>
                  </button>
                )
              })}
            </div>

            <div className="mt-8 border-t border-black/[0.08] pt-4 text-xs text-muted-foreground dark:border-white/[0.08]">
              <div className="mb-1 uppercase tracking-[0.18em]">{t.daemon}</div>
              <div className="break-all">{runtimeAddress}</div>
            </div>

            <div className="mt-6 border-t border-black/[0.08] pt-4 dark:border-white/[0.08]">
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">{t.connectors}</div>
              {connectorSummary.length === 0 ? (
                <div className="text-xs text-muted-foreground">{t.noHealth}</div>
              ) : (
                <div className="space-y-2">
                  {connectorSummary.map((connector) => {
                    const entry = visibleConnectorEntries.find((item) => item.name === connector.name)
                    const Icon = entry?.icon
                    return (
                      <button
                        key={connector.name}
                        type="button"
                        onClick={() => {
                          setSelectedName('connectors')
                          setSaveMessage('')
                          navigate({
                            pathname: settingsConfigPath('connectors', connector.name),
                            hash: '',
                          })
                        }}
                        className="flex w-full items-center justify-between gap-3 rounded-[16px] border border-black/[0.06] bg-white/[0.4] px-3 py-2 text-left text-sm transition hover:border-black/[0.12] hover:text-foreground dark:border-white/[0.08] dark:bg-white/[0.03]"
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          {Icon ? (
                            <span className="flex h-7 w-7 items-center justify-center rounded-full border border-black/[0.06] bg-white/[0.56] dark:border-white/[0.1] dark:bg-white/[0.04]">
                              <Icon className="h-3.5 w-3.5" />
                            </span>
                          ) : null}
                          <span className="truncate">{connector.label}</span>
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">{connector.enabled ? t.enabled : t.idle}</span>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </aside>

          <section ref={contentRef} className="feed-scrollbar min-h-0 overflow-y-auto py-6 xl:px-10">
            {isPageLoading ? (
              <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                {t.loading}...
              </div>
            ) : null}

            {!isPageLoading && !selectedName ? <div className="text-sm text-muted-foreground">{t.noFile}</div> : null}

            {!isPageLoading && selectedName && selectedMeta ? (
              <>
                <header
                  id={selectedAnchorId || undefined}
                  className="flex scroll-mt-4 flex-col gap-4 border-b border-black/[0.08] pb-5 xl:flex-row xl:items-start xl:justify-between dark:border-white/[0.08]"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h1 className="text-3xl font-semibold tracking-tight">{selectedMeta.label[locale]}</h1>
                      <HintDot label={selectedMeta.hint[locale]} />
                      {selectedAnchorId ? (
                        <button
                          type="button"
                          onClick={() => jumpToAnchor(selectedAnchorId)}
                          className="rounded-full border border-black/[0.08] bg-white/[0.44] px-2 py-1 text-[11px] text-muted-foreground transition hover:text-foreground dark:border-white/[0.12] dark:bg-white/[0.03]"
                        >
                          #{selectedAnchorId}
                        </button>
                      ) : null}
                      {isDirty ? <span className="text-xs text-muted-foreground">{t.dirty}</span> : null}
                    </div>
                    {document?.path ? <div className="mt-2 break-all text-xs text-muted-foreground">{document.path}</div> : null}
                  </div>
                </header>

                {saveMessage ? <div className="mt-3 text-sm text-emerald-700 dark:text-emerald-300">{saveMessage}</div> : null}

                {helpMarkdown && !isConnectorDocument && !isBaselineDocument ? (
                  <section className="border-b border-black/[0.08] py-6 dark:border-white/[0.08]">
                    <div className="mb-3 text-sm font-medium">{t.reference}</div>
                    <MarkdownDocument
                      content={helpMarkdown}
                      hideFrontmatter
                      containerClassName="gap-0"
                      bodyClassName="max-h-none overflow-visible rounded-none bg-transparent px-0 py-0 text-sm leading-7 break-words [overflow-wrap:anywhere]"
                    />
                  </section>
                ) : null}

                {document && isConnectorDocument ? (
                  <div className="pt-6">
                    <ConnectorSettingsForm
                      locale={locale}
                      value={structuredConnectors}
                      connectors={connectors}
                      quests={quests}
                      saving={saving}
                      isDirty={isDirty}
                      deletingProfileKey={deletingProfileKey}
                      bindingProfileKey={bindingProfileKey}
                      visibleConnectorNames={visibleConnectorEntries.map((entry) => entry.name)}
                      selectedConnectorName={selectedConnectorName}
                      onChange={setStructuredConnectors}
                      onSave={handleSave}
                      onRefresh={refreshConnectorSettings}
                      onDeleteProfile={(connectorName, profileId) => void handleDeleteConnectorProfile(connectorName, profileId)}
                      onManageProfileBinding={(payload) => handleManageConnectorBinding(payload)}
                      onSelectConnector={(connectorName) =>
                        navigate({
                          pathname: settingsConfigPath('connectors', connectorName),
                          hash: '',
                        })
                      }
                      onBackToConnectorCatalog={() =>
                        navigate({
                          pathname: settingsConfigPath('connectors'),
                          hash: '',
                        })
                      }
                      onJumpToAnchor={jumpToAnchor}
                    />
                  </div>
                ) : null}

                {isBaselineDocument ? (
                  <div className="pt-6">
                    <BaselineSettingsPanel
                      locale={locale}
                      entries={baselineEntries}
                      deletingBaselineId={deletingBaselineId}
                      onDeleteBaseline={handleDeleteBaseline}
                    />
                  </div>
                ) : null}

                {document && !isConnectorDocument && !isBaselineDocument ? (
                  <div className="pt-6">
                    <RegistrySettingsForm
                      documentName={selectedName as Exclude<ConfigDocumentName, 'connectors' | 'baselines'>}
                      locale={locale}
                      value={structuredDraft}
                      validation={validation}
                      testResult={testResult}
                      saving={saving}
                      validating={validating}
                      testingAll={testingAll}
                      systemTestable={Boolean(document.meta?.system_testable)}
                      onChange={setStructuredDraft}
                      onSave={() => void handleSave()}
                      onValidate={() => void runValidate()}
                      onTestAll={() => void runTestAll()}
                    />
                  </div>
                ) : null}
              </>
            ) : null}
          </section>
        </div>
      </main>
    </div>
  )
}
