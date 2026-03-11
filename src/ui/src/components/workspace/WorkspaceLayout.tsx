'use client'

/**
 * WorkspaceLayout - Premium Three-Column Workspace
 *
 * Based on documentation requirements:
 * - Left: File tree + Quick actions (Search, Analysis, Plugins, Settings)
 * - Center: Tab bar + Plugin render area
 * - Right: AI Copilot panel with conversation history
 * - Bottom: Status bar
 */

import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import dynamic from 'next/dynamic'
import { useRouter } from 'next/navigation'
import {
  Search,
  Plus,
  BarChart3,
  Puzzle,
  Settings,
  FilePlus,
  FileText,
  FolderPlus,
  FlaskConical,
  Upload,
  RefreshCw,
  BookOpen,
  Sparkles,
  MoreHorizontal,
  ArrowLeft,
  ChevronLeft,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FolderOpen,
  Github,
  LayoutTemplate,
  Share2,
  KeyRound,
  Braces,
  Terminal,
  X,
} from 'lucide-react'
import { useAuthStore } from '@/lib/stores/auth'
import { useFileTreeStore } from '@/lib/stores/file-tree'
import { useTabsStore, useActiveTab } from '@/lib/stores/tabs'
import { useChatSessionStore } from '@/lib/stores/session'
import { useLabCopilotStore } from '@/lib/stores/lab-copilot'
import { useOpenFile } from '@/hooks/useOpenFile'
import { useProject, useUpdateProject } from '@/lib/hooks/useProjects'
import { createNotebook, getNotebook, listNotebooks } from '@/lib/api/notebooks'
import { getMyToken, rotateMyToken } from '@/lib/api/auth'
import { checkProjectAccess } from '@/lib/api/projects'
import { listLabAgents, listLabQuests, listLabTemplates } from '@/lib/api/lab'
import { useCliStore } from '@/lib/plugins/cli/stores/cli-store'
import { CreateFileDialog, CreateLatexProjectDialog, FileIcon, FileTree } from '@/components/file-tree'
import { PluginRenderer } from '@/components/plugin'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Icon3D } from '@/components/ui/icon-3d'
import { PngIcon } from '@/components/ui/png-icon'
import { DotfilesToggleIcon } from '@/components/ui/dotfiles-toggle-icon'
import { type AiManusChatActions, type CopilotPrefill } from '@/lib/plugins/ai-manus/view-types'
import { TokenDialog } from '@/components/auth/TokenDialog'
import { ProjectShareDialog } from '@/components/features/Share/ProjectShareDialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { useToast } from '@/components/ui/toast'
import { ConfirmModal } from '@/components/ui/modal'
import { FadeContent, Noise, SpotlightCard } from '@/components/react-bits'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { BUILTIN_PLUGINS } from '@/lib/types/plugin'
import type { FileNode } from '@/lib/types/file'
import type { Tab } from '@/lib/types/tab'
import { searchFileNodes } from '@/lib/search/file-search'
import { SearchIcon, SettingsIcon, SparklesIcon, LayoutIcon } from '@/components/ui/workspace-icons'
import { CopilotDockOverlay } from '@/components/workspace/CopilotDockOverlay'
import { COPILOT_DOCK_DEFAULTS, useCopilotDockState } from '@/hooks/useCopilotDockState'
import { getSharedProjects } from '@/lib/shared-projects'
import { isShareViewForProject } from '@/lib/share-session'
import { useI18n } from '@/lib/i18n/useI18n'
import { WorkspaceTooltipLayer } from '@/components/workspace/WorkspaceTooltipLayer'
import { WelcomeStage } from '@/components/workspace/WelcomeStage'
import { QuestCopilotDockPanel } from '@/components/workspace/QuestCopilotDockPanel'
import { QuestWorkspaceSurface } from '@/components/workspace/QuestWorkspaceSurface'
import { NotificationBell } from '@/components/ui/notification-bell'
import {
  EXPLORER_REFRESH_EVENT,
  type ExplorerRefreshDetail,
} from '@/lib/plugins/lab/lib/explorer-events'
import {
  WORKSPACE_LEFT_VISIBILITY_EVENT,
  type QuestWorkspaceView,
  type WorkspaceLeftVisibilityDetail,
} from './workspace-events'

const LabCopilotPanel = dynamic(() => import('@/lib/plugins/lab/components/LabCopilotPanel'), {
  ssr: false,
  loading: () => null,
})
const LabCopilotHeader = dynamic(
  () => import('@/lib/plugins/lab/components/LabCopilotPanel').then((mod) => mod.LabCopilotHeader),
  { ssr: false, loading: () => null }
)

const getLabContextSessionId = (tab: Tab | null | undefined, projectId?: string | null) => {
  if (!tab) return null
  const customData = tab.context?.customData
  if (!customData || typeof customData !== 'object') return null
  const record = customData as Record<string, unknown>
  if (record.lab_context !== true) return null
  if (projectId && typeof record.projectId === 'string' && record.projectId !== projectId) return null
  const sessionId = typeof record.lab_session_id === 'string' ? record.lab_session_id : null
  return sessionId && sessionId.trim() ? sessionId : null
}

// ============================================================================
// Types
// ============================================================================

interface WorkspaceLayoutProps {
  projectId: string
  projectName?: string
  projectSource?: string | null
  readOnly?: boolean
  isSharedView?: boolean
}

type CommandGroup = 'Quick' | 'Files' | 'Create' | 'Navigate' | 'Panels' | 'Access' | 'Tools'
type ScrollbarSide = 'left' | 'right'
type CopilotSurfaceMode = 'agent' | 'lab'

const WORKSPACE_ENTRY_HOLD_MS = 120
const WORKSPACE_ENTRY_ANIM_MS = 720
const HOME_SWITCH_MS = 500
const TAB_SWITCH_MS = 500
const COPILOT_SWITCH_MIN_SEC = 0.28
const COPILOT_SWITCH_MAX_SEC = 0.82
const MARKDOWN_EXTENSIONS = ['.md', '.markdown', '.mdx']
const MARKDOWN_MIME_TYPES = new Set(['text/markdown', 'text/x-markdown'])
const QUEST_WORKSPACE_PLUGIN_ID = '@ds/plugin-quest-workspace'

function scheduleIdle(work: () => void, timeoutMs = 1200) {
  if (typeof window === 'undefined') {
    work()
    return () => {}
  }
  const win = window as Window & {
    requestIdleCallback?: (cb: IdleRequestCallback, opts?: IdleRequestOptions) => number
    cancelIdleCallback?: (id: number) => void
  }
  if (win.requestIdleCallback) {
    const id = win.requestIdleCallback(() => work(), { timeout: timeoutMs })
    return () => win.cancelIdleCallback?.(id)
  }
  const timer = window.setTimeout(work, timeoutMs)
  return () => window.clearTimeout(timer)
}

function isMarkdownContext(resourceName?: string, mimeType?: string, docKind?: unknown) {
  if (docKind === 'markdown') return true
  if (mimeType && MARKDOWN_MIME_TYPES.has(mimeType)) return true
  if (!resourceName) return false
  const lower = resourceName.toLowerCase()
  return MARKDOWN_EXTENSIONS.some((ext) => lower.endsWith(ext))
}

function getCopilotSwitchDurationMs(stageWidth: number, dockWidth: number) {
  const boundsWidth = Math.max(0, stageWidth - COPILOT_DOCK_DEFAULTS.edgeInset * 2)
  const maxX = Math.max(0, boundsWidth - dockWidth)
  const durationSec = Math.min(
    COPILOT_SWITCH_MAX_SEC,
    Math.max(COPILOT_SWITCH_MIN_SEC, maxX / 900)
  )
  return Math.round(durationSec * 1000)
}

function getProjectIdFromTab(tab: { context?: { customData?: Record<string, unknown> } } | null | undefined) {
  const customData = tab?.context?.customData as { projectId?: unknown } | undefined
  return typeof customData?.projectId === 'string' ? customData.projectId : null
}

function tabMatchesProject(
  tab: { context?: { customData?: Record<string, unknown> } } | null | undefined,
  projectId: string
) {
  const tabProjectId = getProjectIdFromTab(tab)
  return !tabProjectId || tabProjectId === projectId
}

function tabIsForeignProject(
  tab: { context?: { customData?: Record<string, unknown> } } | null | undefined,
  projectId: string
) {
  const tabProjectId = getProjectIdFromTab(tab)
  return Boolean(tabProjectId && tabProjectId !== projectId)
}

function buildQuestWorkspaceTabContext(projectId: string, view: QuestWorkspaceView) {
  return {
    type: 'custom' as const,
    customData: {
      projectId,
      quest_workspace: true,
      quest_workspace_view: view,
    },
  }
}

function isQuestWorkspaceTab(
  tab: { pluginId?: string; context?: { customData?: Record<string, unknown> } } | null | undefined,
  projectId?: string
) {
  if (!tab || tab.pluginId !== QUEST_WORKSPACE_PLUGIN_ID) {
    return false
  }
  if (!projectId) {
    return true
  }
  return tabMatchesProject(tab, projectId)
}

function getQuestWorkspaceTabView(
  tabOrContext:
    | { context?: { customData?: Record<string, unknown> } }
    | { customData?: Record<string, unknown> }
    | null
    | undefined
): QuestWorkspaceView {
  const customData =
    'context' in (tabOrContext || {})
      ? tabOrContext?.context?.customData
      : tabOrContext?.customData
  if (customData?.quest_workspace_view === 'details') return 'details'
  if (customData?.quest_workspace_view === 'terminal') return 'terminal'
  return 'canvas'
}

function getQuestWorkspaceTitle(view: QuestWorkspaceView) {
  if (view === 'details') return 'Details'
  if (view === 'terminal') return 'Terminal'
  return 'Canvas'
}

function isQuestFriendlyTab(
  tab: { pluginId?: string; context?: { customData?: Record<string, unknown> } } | null | undefined,
  projectId?: string
) {
  if (!tab) return false
  if (isQuestWorkspaceTab(tab, projectId)) return true
  return [
    BUILTIN_PLUGINS.PDF_VIEWER,
    BUILTIN_PLUGINS.PDF_MARKDOWN,
    BUILTIN_PLUGINS.NOTEBOOK,
    BUILTIN_PLUGINS.LATEX,
    BUILTIN_PLUGINS.CODE_EDITOR,
    BUILTIN_PLUGINS.CODE_VIEWER,
    BUILTIN_PLUGINS.IMAGE_VIEWER,
    BUILTIN_PLUGINS.TEXT_VIEWER,
    BUILTIN_PLUGINS.MARKDOWN_VIEWER,
  ].includes(tab.pluginId as (typeof BUILTIN_PLUGINS)[keyof typeof BUILTIN_PLUGINS])
}

type CommandItem = {
  id: string
  title: string
  description?: string
  group: CommandGroup
  keywords?: string[]
  shortcut?: string
  icon?: React.ReactNode
  run: () => void
}

function matchCommand(query: string, item: CommandItem): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  const tokens = q.split(/\s+/).filter(Boolean).slice(0, 6)
  if (tokens.length === 0) return true

  const haystack = [item.title, item.description ?? '', ...(item.keywords ?? [])]
    .join(' ')
    .toLowerCase()

  return tokens.every((t) => haystack.includes(t))
}

const TOKEN_PREFIXES = ['t', 'to', 'tok', 'toke', 'token']
const SHARE_PREFIXES = ['s', 'sh', 'sha', 'share']
const IMPORT_PREFIXES = ['i', 'im', 'imp', 'impo', 'impor', 'import']
const NEW_PREFIXES = ['n', 'ne', 'new']
const TEMPLATE_PREFIXES = ['te', 'tem', 'temp', 'templ', 'templa', 'templat', 'template']
const UUID_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const matchPrefix = (query: string, prefixes: string[]) =>
  prefixes.some((prefix) =>
    query === prefix ||
    query.startsWith(`${prefix} `) ||
    query.startsWith(`${prefix}:`) ||
    query.startsWith(`${prefix}/`)
  )

const extractShareToken = (value: string) => {
  const match = value.match(/\/share\/([^/?#\s]+)/i)
  if (match?.[1]) return match[1]
  const inlineMatch = value.match(/^share[:\s/]+([^/?#\s]+)/i)
  if (inlineMatch?.[1]) return inlineMatch[1]
  return null
}

const extractGithubUrl = (value: string) => {
  const match = value.match(/(?:https?:\/\/|git@)?github\.com[^\s]+/i)
  if (!match) return null
  const raw = match[0]
  if (raw.startsWith('http')) return raw
  if (raw.startsWith('git@')) {
    const normalized = raw.replace('git@', '').replace(':', '/')
    return `https://${normalized}`
  }
  return `https://${raw}`
}

function WorkspaceCommandPalette({
  open,
  onOpenChange,
  items,
  projectId,
  onExitHome,
  tokenActions,
  onEnterLab,
  onOpenShareDialog,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  items: CommandItem[]
  projectId: string
  onExitHome?: () => void
  tokenActions?: { onGetToken: () => void; onRefreshToken: () => void; hasToken: boolean }
  onEnterLab?: () => void
  onOpenShareDialog?: () => void
}) {
  const { t } = useI18n('workspace')
  const router = useRouter()
  const inputRef = React.useRef<HTMLInputElement>(null)
  const contentRef = React.useRef<HTMLDivElement>(null)
  const [query, setQuery] = React.useState('')
  const [activeIndex, setActiveIndex] = React.useState(0)
  const nodes = useFileTreeStore((s) => s.nodes)
  const storeProjectId = useFileTreeStore((s) => s.projectId)
  const isLoading = useFileTreeStore((s) => s.isLoading)
  const loadFiles = useFileTreeStore((s) => s.loadFiles)
  const { openFileInTab } = useOpenFile()

  React.useEffect(() => {
    if (!open) return
    setQuery('')
    setActiveIndex(0)
    const t = window.setTimeout(() => inputRef.current?.focus(), 60)
    return () => window.clearTimeout(t)
  }, [open])

  React.useEffect(() => {
    if (!open) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onOpenChange(false)
      }
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (!target) return
      if (contentRef.current && contentRef.current.contains(target)) return
      onOpenChange(false)
    }
    document.addEventListener('keydown', handleKeyDown, { capture: true })
    document.addEventListener('pointerdown', handlePointerDown, { capture: true })
    return () => {
      document.removeEventListener('keydown', handleKeyDown, { capture: true })
      document.removeEventListener('pointerdown', handlePointerDown, { capture: true })
    }
  }, [onOpenChange, open])

  React.useEffect(() => {
    if (!open) return
    if (!projectId) return
    if (storeProjectId === projectId && nodes.length > 0) return
    void loadFiles(projectId)
  }, [loadFiles, nodes.length, open, projectId, storeProjectId])

  const trimmedQuery = query.trim()
  const normalizedQuery = trimmedQuery.toLowerCase()
  const showTokenActions =
    normalizedQuery.startsWith('token') || TOKEN_PREFIXES.includes(normalizedQuery)
  const shareToken = extractShareToken(trimmedQuery)
  const githubUrl = extractGithubUrl(trimmedQuery)
  const isUuid = UUID_REGEX.test(trimmedQuery)
  const showShareActions = matchPrefix(normalizedQuery, SHARE_PREFIXES) || Boolean(shareToken)
  const showImportActions = matchPrefix(normalizedQuery, IMPORT_PREFIXES)
  const showNewActions = matchPrefix(normalizedQuery, NEW_PREFIXES)
  const showTemplateActions = matchPrefix(normalizedQuery, TEMPLATE_PREFIXES)

  const autocompleteKeyword = () => {
    const candidates: Array<{ prefixes: string[]; value: string }> = [
      { prefixes: TOKEN_PREFIXES, value: 'token' },
      { prefixes: SHARE_PREFIXES, value: 'share' },
      { prefixes: IMPORT_PREFIXES, value: 'import' },
      { prefixes: NEW_PREFIXES, value: 'new' },
      { prefixes: TEMPLATE_PREFIXES, value: 'template' },
    ]
    const match = candidates.find((candidate) => candidate.prefixes.includes(normalizedQuery))
    if (!match) return false
    setQuery(`${match.value} `)
    return true
  }

  const tokenItems = React.useMemo<CommandItem[]>(() => {
    if (!showTokenActions || !tokenActions) return []
    return [
      {
        id: 'token:get',
        title: t('command_get_access_token_title'),
        description: t('command_get_access_token_desc'),
        group: 'Access',
        keywords: ['token', 'access', 'api'],
        icon: <KeyRound className="h-4 w-4 text-muted-foreground" />,
        run: () => {
          tokenActions.onGetToken()
          onOpenChange(false)
        },
      },
      {
        id: 'token:refresh',
        title: tokenActions.hasToken
          ? t('command_refresh_access_token_title')
          : t('command_refresh_access_token_requires_title'),
        description: tokenActions.hasToken
          ? t('command_refresh_access_token_desc')
          : t('command_refresh_access_token_missing_desc'),
        group: 'Access',
        keywords: ['token', 'refresh', 'rotate'],
        icon: <RefreshCw className="h-4 w-4 text-muted-foreground" />,
        run: () => {
          if (!tokenActions.hasToken) {
            tokenActions.onGetToken()
          } else {
            tokenActions.onRefreshToken()
          }
          onOpenChange(false)
        },
      },
    ]
  }, [onOpenChange, showTokenActions, t, tokenActions])

  const itemMap = React.useMemo(() => new Map(items.map((item) => [item.id, item])), [items])

  const smartItems = React.useMemo<CommandItem[]>(() => {
    if (showTokenActions) return []
    const next: CommandItem[] = []

    const addQuick = (id: string) => {
      const base = itemMap.get(id)
      if (!base) return
      next.push({ ...base, group: 'Quick' })
    }

    const addQuickSet = (ids: string[]) => {
      ids.forEach((id) => addQuick(id))
    }

    if (!normalizedQuery) {
      addQuickSet(['new-notebook', 'new-file', 'upload-files', 'search', 'settings'])
    }

    if (showNewActions) {
      addQuickSet(['new-notebook', 'new-file', 'new-folder', 'new-latex-project'])
    }

    if (showImportActions) {
      addQuick('upload-files')
    }

    if (showTemplateActions && onEnterLab) {
      next.push({
        id: 'lab:templates',
        title: t('command_open_lab_templates_title'),
        description: t('command_open_lab_templates_desc'),
        group: 'Quick',
        keywords: ['template', 'lab', 'copilot'],
        icon: <LayoutTemplate className="h-4 w-4 text-muted-foreground" />,
        run: () => {
          onEnterLab()
        },
      })
    }

    if (showShareActions) {
      if (onOpenShareDialog) {
        next.push({
          id: 'share:project',
          title: t('command_share_project_title'),
          description: t('command_share_project_desc'),
          group: 'Quick',
          keywords: ['share', 'link', 'copy'],
          icon: <Share2 className="h-4 w-4 text-muted-foreground" />,
          run: () => {
            onOpenShareDialog()
          },
        })
      }
      if (shareToken) {
        next.push({
          id: 'share:open-link',
          title: t('command_open_share_link_title'),
          description: `share/${shareToken}`,
          group: 'Quick',
          keywords: ['share', 'open'],
          icon: <ExternalLink className="h-4 w-4 text-muted-foreground" />,
          run: () => {
            router.push(`/share/${shareToken}`)
          },
        })
      }
    }

    if (isUuid) {
        next.push({
          id: 'project:open-id',
          title: t('command_open_project_by_id_title'),
          description: trimmedQuery,
          group: 'Quick',
          keywords: ['project', 'open'],
        icon: <FolderOpen className="h-4 w-4 text-muted-foreground" />,
        run: () => {
          router.push(`/projects/${trimmedQuery}`)
        },
      })
    }

    if (githubUrl) {
        next.push({
          id: 'github:open',
          title: t('command_open_github_title'),
          description: githubUrl,
          group: 'Quick',
          keywords: ['github', 'repo', 'link'],
        icon: <Github className="h-4 w-4 text-muted-foreground" />,
        run: () => {
          window.open(githubUrl, '_blank', 'noopener,noreferrer')
        },
      })
    }

    return next
  }, [
    githubUrl,
    isUuid,
    itemMap,
    normalizedQuery,
    onEnterLab,
    onOpenShareDialog,
    router,
    shareToken,
    showImportActions,
    showNewActions,
    showShareActions,
    showTemplateActions,
    showTokenActions,
    t,
    trimmedQuery,
  ])

  const filtered = React.useMemo(() => {
    const base = items.filter((item) => matchCommand(query, item))
    const combined = [...smartItems, ...tokenItems, ...base]
    const seen = new Set<string>()
    return combined.filter((item) => {
      if (seen.has(item.id)) return false
      seen.add(item.id)
      return true
    })
  }, [items, query, smartItems, tokenItems])

  const fileMatches = React.useMemo<CommandItem[]>(() => {
    const q = query.trim()
    if (!q) return []

    const scored = searchFileNodes(nodes, q, { limit: 30, includeFolders: false })

    return scored.map((entry) => ({
      id: `file:${entry.node.id}`,
      title: entry.node.name,
      description: entry.node.path || '—',
      group: 'Files',
      keywords: [entry.node.name, entry.node.path || ''],
      icon: (
        <FileIcon
          type={entry.node.type}
          mimeType={entry.node.mimeType}
          name={entry.node.name}
          className="h-4 w-4 text-muted-foreground"
        />
      ),
      run: () => {
        onExitHome?.()
        const options = projectId ? { customData: { projectId } } : undefined
        void openFileInTab(entry.node, options)
      },
    }))
  }, [nodes, openFileInTab, projectId, query])

  const filteredWithFiles = React.useMemo(
    () => [...fileMatches, ...filtered],
    [fileMatches, filtered]
  )

  React.useEffect(() => {
    setActiveIndex(0)
  }, [query])

  const groups = React.useMemo(() => {
    const byGroup = new Map<CommandGroup, CommandItem[]>()
    for (const item of filteredWithFiles) {
      const list = byGroup.get(item.group) ?? []
      list.push(item)
      byGroup.set(item.group, list)
    }
    const order: CommandGroup[] = ['Quick', 'Files', 'Create', 'Navigate', 'Panels', 'Access', 'Tools']
    return order
      .filter((g) => (byGroup.get(g)?.length ?? 0) > 0)
      .map((g) => ({ group: g, items: byGroup.get(g)! }))
  }, [filteredWithFiles])

  const groupLabels = React.useMemo<Record<CommandGroup, string>>(
    () => ({
      Quick: t('command_group_quick'),
      Files: t('command_group_files'),
      Create: t('command_group_create'),
      Navigate: t('command_group_navigate'),
      Panels: t('command_group_panels'),
      Access: t('command_group_access'),
      Tools: t('command_group_tools'),
    }),
    [t]
  )

  const flat = React.useMemo(() => groups.flatMap((g) => g.items), [groups])
  const safeActiveIndex = Math.min(activeIndex, Math.max(0, flat.length - 1))

  const runItem = (item: CommandItem) => {
    item.run()
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent ref={contentRef} className="sm:max-w-2xl p-0 overflow-hidden">
        <div className="p-4 border-b border-border/50 bg-white/60 backdrop-blur-xl dark:bg-black/40">
          <DialogHeader>
            <DialogTitle className="text-base">{t('command_palette_title')}</DialogTitle>
            <DialogDescription className="text-left">
              {t('command_palette_description')}
            </DialogDescription>
          </DialogHeader>
          <div className="mt-3">
            <Input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('command_palette_placeholder')}
              className="h-10 rounded-full bg-white/70 border-black/5 dark:bg-white/[0.04] dark:border-white/10"
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  e.preventDefault()
                  onOpenChange(false)
                } else if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  setActiveIndex((i) => Math.min(i + 1, Math.max(0, flat.length - 1)))
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  setActiveIndex((i) => Math.max(i - 1, 0))
                } else if (e.key === 'Enter') {
                  e.preventDefault()
                  const item = flat[safeActiveIndex]
                  if (item) runItem(item)
                } else if (e.key === 'Tab' && !e.shiftKey) {
                  const didAutocomplete = autocompleteKeyword()
                  if (didAutocomplete) {
                    e.preventDefault()
                  }
                }
              }}
            />
          </div>
        </div>

        <ScrollArea className="max-h-[420px]">
          <div className="p-2">
            {flat.length === 0 ? (
              <div className="p-6 text-sm text-muted-foreground text-center">
                {t('command_palette_no_results')}
              </div>
            ) : (
              groups.map(({ group, items }) => (
                <div key={group} className="mb-2 last:mb-0">
                  <div className="px-2 py-2 text-xs font-medium text-muted-foreground">
                    {groupLabels[group]}
                  </div>
                  <div className="space-y-1">
                    {items.map((item) => {
                      const idx = flat.findIndex((x) => x.id === item.id)
                      const isActive = idx === safeActiveIndex
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onMouseEnter={() => setActiveIndex(idx)}
                          onClick={() => runItem(item)}
                          className={cn(
                            'w-full flex items-start gap-3 rounded-xl px-3 py-2 text-left transition-colors',
                            'hover:bg-muted/40',
                            isActive && 'bg-muted/50'
                          )}
                        >
                          <div className="mt-0.5 h-8 w-8 rounded-lg border border-black/5 bg-white/60 flex items-center justify-center dark:bg-white/[0.04] dark:border-white/10">
                            {item.icon ?? (
                              <PngIcon
                                name="Search"
                                size={16}
                                className="h-4 w-4"
                                fallback={<Search className="h-4 w-4 text-muted-foreground" />}
                              />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <div className="text-sm font-medium truncate">{item.title}</div>
                              {item.shortcut && (
                                <kbd className="ml-auto px-2 py-0.5 rounded-full border text-[10px] text-muted-foreground bg-white/50 border-black/5 dark:bg-white/[0.03] dark:border-white/10">
                                  {item.shortcut}
                                </kbd>
                              )}
                            </div>
                            {item.description && (
                              <div className="mt-0.5 text-xs text-muted-foreground line-clamp-1">
                                {item.description}
                              </div>
                            )}
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>
              ))
            )}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}

// ============================================================================
// Navbar Component
// ============================================================================

function Navbar({
  projectId,
  projectName,
  onToggleLeft,
  onToggleRight,
  onOpenCommandPalette,
  onOpenSettings,
  onOpenProjectSettings,
  onShare,
  showShare,
  onNewNotebook,
  onNewFile,
  onNewLatexProject,
  onNewFolder,
  onUploadFiles,
  leftVisible,
  rightVisible,
  rightLocked,
  readOnly,
  collapsed,
  onToggleCollapse,
  onExitHome,
  onTabSelect,
  localQuestMode = false,
}: {
  projectId: string
  projectName?: string
  onToggleLeft: () => void
  onToggleRight: () => void
  onOpenCommandPalette: () => void
  onOpenSettings: () => void
  onOpenProjectSettings: () => void
  onShare?: () => void
  showShare?: boolean
  onNewNotebook: () => void
  onNewFile: () => void
  onNewLatexProject: () => void
  onNewFolder: () => void
  onUploadFiles: () => void
  leftVisible: boolean
  rightVisible: boolean
  rightLocked?: boolean
  readOnly?: boolean
  collapsed: boolean
  onToggleCollapse: () => void
  onExitHome?: () => void
  onTabSelect?: (tabId: string) => void
  localQuestMode?: boolean
}) {
  const { t } = useI18n('workspace')
  const router = useRouter()
  const openTab = useTabsStore((state) => state.openTab)
  const readOnlyMode = Boolean(readOnly)
  const { addToast } = useToast()
  const updateProject = useUpdateProject()
  const { data: project } = useProject(projectId, {
    enabled: !readOnlyMode && Boolean(projectId) && !projectName && !localQuestMode,
  })
  const projectDisplayName = project?.name ?? projectName ?? (projectId ? `Project ${projectId}` : 'Project')
  const canRename = !readOnlyMode && Boolean(projectId)
  const [isRenaming, setIsRenaming] = React.useState(false)
  const [draftName, setDraftName] = React.useState(projectDisplayName)
  const [isSavingName, setIsSavingName] = React.useState(false)
  const nameInputRef = React.useRef<HTMLInputElement>(null)
  const cancelRenameRef = React.useRef(false)

  React.useEffect(() => {
    if (!isRenaming) {
      setDraftName(projectDisplayName)
    }
  }, [isRenaming, projectDisplayName])

  React.useEffect(() => {
    if (!isRenaming) return
    if (typeof window === 'undefined') return
    const t = window.setTimeout(() => nameInputRef.current?.focus(), 0)
    return () => window.clearTimeout(t)
  }, [isRenaming])

  const handleStartRename = React.useCallback(() => {
    if (!canRename) return
    cancelRenameRef.current = false
    setIsRenaming(true)
  }, [canRename])

  const handleCancelRename = React.useCallback(() => {
    cancelRenameRef.current = true
    setDraftName(projectDisplayName)
    setIsRenaming(false)
  }, [projectDisplayName])

  const handleCommitRename = React.useCallback(async () => {
    if (!canRename || isSavingName) return
    const nextName = draftName.trim()
    if (!nextName) {
      addToast({
        type: 'error',
        title: t('toast_project_name_required'),
        description: t('toast_enter_project_name'),
      })
      setDraftName(projectDisplayName)
      setIsRenaming(false)
      return
    }
    if (nextName === projectDisplayName) {
      setIsRenaming(false)
      return
    }
    setIsSavingName(true)
    try {
      await updateProject.mutateAsync({
        projectId,
        data: { name: nextName },
      })
      setIsRenaming(false)
    } catch (err) {
      const message = err instanceof Error ? err.message : t('toast_rename_failed')
      addToast({
        type: 'error',
        title: t('toast_rename_failed'),
        description: message,
      })
      setDraftName(projectDisplayName)
      setIsRenaming(false)
    } finally {
      setIsSavingName(false)
    }
  }, [
    addToast,
    canRename,
    draftName,
    isSavingName,
    projectDisplayName,
    projectId,
    updateProject,
  ])

  const handleOpenCli = React.useCallback(() => {
    onExitHome?.()
    if (localQuestMode) {
      openTab({
        pluginId: QUEST_WORKSPACE_PLUGIN_ID,
        context: buildQuestWorkspaceTabContext(projectId, 'terminal'),
        title: getQuestWorkspaceTitle('terminal'),
      })
      return
    }
    openTab({
      pluginId: BUILTIN_PLUGINS.CLI,
      context: { type: 'custom', customData: { projectId, readOnly: readOnlyMode } },
      title: t('plugin_cli_title'),
    })
  }, [localQuestMode, onExitHome, openTab, projectId, readOnlyMode, t])

  const showCompactNavbar = collapsed
  const shareLabel = readOnlyMode ? t('navbar_copy_share') : t('navbar_share')
  const handleGoProjects = React.useCallback(() => {
    onExitHome?.()
    router.push('/projects')
  }, [onExitHome, router])

  return (
    <>
      <nav className={cn('navbar', collapsed && 'is-collapsed')}>
        {showCompactNavbar ? (
          <div className="navbar-roll">
            <button
              type="button"
              onClick={onToggleCollapse}
              className="ghost-btn navbar-roll-toggle"
              aria-label={t('navbar_expand_topbar')}
              data-tooltip={t('navbar_expand_topbar')}
            >
              <ChevronRight className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={onToggleLeft}
              className={cn('ghost-btn navbar-roll-btn', leftVisible && 'is-active')}
              aria-label={leftVisible ? t('navbar_hide_explorer') : t('navbar_show_explorer')}
              data-tooltip={leftVisible ? t('navbar_hide_explorer') : t('navbar_show_explorer')}
            >
              <LayoutIcon className="h-4 w-4" />
            </button>
            {!readOnlyMode && (
              <button
                type="button"
                onClick={rightLocked ? undefined : onToggleRight}
                className={cn(
                  'ghost-btn navbar-roll-btn',
                  rightVisible && 'is-active',
                  rightLocked && 'opacity-80 cursor-default'
                )}
                aria-disabled={rightLocked}
                aria-label={
                  rightLocked
                    ? t('navbar_copilot_active_on_agent')
                    : rightVisible
                      ? t('navbar_hide_copilot')
                      : t('navbar_show_copilot')
                }
                data-tooltip={
                  rightLocked
                    ? t('navbar_copilot_active_on_agent')
                    : rightVisible
                      ? t('navbar_hide_copilot')
                      : t('navbar_show_copilot')
                }
              >
                <SparklesIcon className="h-4 w-4" />
              </button>
            )}
            <div className="navbar-roll-actions">
              {showShare && onShare && (
                <button
                  type="button"
                  className="ghost-btn navbar-roll-btn"
                  aria-label={shareLabel}
                  data-tooltip={shareLabel}
                  onClick={onShare}
                >
                  <Share2 className="h-4 w-4" />
                </button>
              )}
              <button
                type="button"
                className="navbar-roll-link bg-transparent border-0"
                onClick={handleGoProjects}
                aria-label="DeepScientist"
                data-tooltip="DeepScientist"
              >
                DeepScientist
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Left: Branding + Project */}
            <div className="app-branding">
              <div className="navbar-left-controls">
                <button
                  onClick={onToggleLeft}
                  className={cn('ghost-btn', leftVisible && 'is-active')}
              aria-label={leftVisible ? t('navbar_hide_explorer') : t('navbar_show_explorer')}
              data-tooltip={leftVisible ? t('navbar_hide_explorer') : t('navbar_show_explorer')}
                >
                  <LayoutIcon />
                </button>
                <button
                  type="button"
                  className="ghost-btn"
                  aria-label={t('navbar_collapse_topbar')}
                  data-tooltip={t('navbar_collapse_topbar')}
                  onClick={onToggleCollapse}
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
              </div>
              <div className="user-identity">
                <button
                  type="button"
                  className="user-menu-trigger"
                  onClick={handleGoProjects}
                  aria-label="DeepScientist"
                  data-tooltip="DeepScientist"
                >
                  <span className="user-menu-name">DeepScientist</span>
                </button>
              </div>
              {projectDisplayName && (
                <>
                  <span className="mx-2 text-[var(--text-muted)]">/</span>
                  {isRenaming && canRename ? (
                    <div className="project-name-field is-editing">
                      <input
                        ref={nameInputRef}
                        value={draftName}
                        onChange={(e) => setDraftName(e.target.value)}
                        onBlur={() => {
                          if (cancelRenameRef.current) {
                            cancelRenameRef.current = false
                            return
                          }
                          void handleCommitRename()
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault()
                            void handleCommitRename()
                          }
                          if (e.key === 'Escape') {
                            e.preventDefault()
                            handleCancelRename()
                          }
                        }}
                        className="project-name-input"
                        aria-label={t('navbar_project_name')}
                        disabled={isSavingName}
                      />
                    </div>
                  ) : (
                    <button
                      type="button"
                      className={cn(
                        'project-name-field',
                        canRename ? 'is-button' : 'is-disabled'
                      )}
                      onClick={handleStartRename}
                      disabled={!canRename}
                      title={canRename ? t('navbar_rename_project') : t('navbar_readonly_project')}
                    >
                      <span className="truncate w-full">{projectDisplayName}</span>
                    </button>
                  )}
                </>
              )}
            </div>

            {/* Center: Tabs + Search */}
            <div className="flex-1 hidden md:flex items-center min-w-0">
              <div className="flex w-full items-center gap-2 min-w-0">
                <div className="flex-1 min-w-0">
                  <WorkspaceTabStrip
                    projectId={projectId}
                    onTabSelect={onTabSelect}
                    localQuestMode={localQuestMode}
                  />
                </div>
                <button
                  type="button"
                  onClick={onOpenCommandPalette}
                  className={cn(
                    'group inline-flex items-center gap-2 rounded-full border border-black/10 bg-white/70 px-3 py-1.5 text-sm text-[var(--text-main)]',
                    'shadow-[0_1px_2px_rgba(0,0,0,0.04)] transition',
                    'hover:border-black/20 hover:bg-white/90',
                    'dark:border-white/15 dark:bg-white/[0.08] dark:text-white/80',
                    'dark:hover:bg-white/[0.14] dark:hover:text-white'
                  )}
                  title={t('navbar_search_hotkey')}
                >
                  <SearchIcon />
                  <span className="truncate">{t('navbar_search')}</span>
                  <kbd className="ml-1 rounded-full border border-black/10 bg-white/80 px-2 py-0.5 text-[10px] text-[var(--text-muted)] dark:border-white/10 dark:bg-white/[0.08] dark:text-white/60">
                    ⌘K
                  </kbd>
                </button>
              </div>
            </div>

            {/* Right: Actions + User */}
            <div className="nav-actions">
              <NotificationBell
                variant="workspace"
                size="sm"
                enabled={!readOnlyMode}
              />
              <button
                className="ghost-btn"
                onClick={handleOpenCli}
                aria-label={t('plugin_cli_title')}
                data-tooltip={t('plugin_cli_title')}
              >
                <Terminal className="h-4 w-4" />
              </button>
              <button
                className="ghost-btn md:hidden"
                onClick={onOpenCommandPalette}
                aria-label={t('navbar_search_hotkey')}
                data-tooltip={t('navbar_search_hotkey')}
              >
                <SearchIcon />
              </button>
              {showShare && onShare && (
                <Button
                  type="button"
                  size="sm"
                  className="h-8 px-3 rounded-full overflow-hidden ds-glare-sheen"
                  title={shareLabel}
                  onClick={onShare}
                >
                  <Plus className="h-4 w-4 mr-2" />
                  {t('navbar_share')}
                </Button>
              )}
              {!readOnlyMode && (
                <>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        className="ghost-btn"
                        aria-label={t('navbar_new')}
                        data-tooltip={t('navbar_new')}
                      >
                        <Plus className="h-4 w-4" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-56">
                      <DropdownMenuItem onClick={onNewNotebook}>
                        <PngIcon
                          name="BookOpen"
                          size={16}
                          className="mr-2 h-4 w-4"
                          fallback={<BookOpen className="mr-2 h-4 w-4" />}
                        />
                        {t('navbar_new_notebook')}
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={onNewFile}>
                        <FileText className="mr-2 h-4 w-4" />
                        {t('navbar_new_file')}
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={onNewLatexProject}>
                        <PngIcon
                          name="Braces"
                          size={16}
                          className="mr-2 h-4 w-4"
                          fallback={<Braces className="mr-2 h-4 w-4" />}
                        />
                        {t('navbar_new_latex')}
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={onNewFolder}>
                        <FolderPlus className="mr-2 h-4 w-4" />
                        {t('command_new_folder_title')}
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={onUploadFiles}>
                        <Upload className="mr-2 h-4 w-4" />
                        {t('navbar_upload_files')}
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onClick={onOpenCommandPalette}>
                        <PngIcon
                          name="Search"
                          size={16}
                          className="mr-2 h-4 w-4"
                          fallback={<Search className="mr-2 h-4 w-4" />}
                        />
                        {t('navbar_search')}…
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>

                  <button
                    className="ghost-btn"
                    onClick={onOpenProjectSettings}
                    aria-label={t('navbar_settings')}
                    data-tooltip={t('navbar_settings')}
                  >
                    <SettingsIcon />
                  </button>
                  <button
                    onClick={rightLocked ? undefined : onToggleRight}
                    className={cn(
                      'ghost-btn',
                      rightVisible && 'is-active',
                      rightLocked && 'opacity-80 cursor-default'
                    )}
                    aria-disabled={rightLocked}
                    aria-label={
                      rightLocked
                        ? t('navbar_copilot_active_on_agent')
                        : rightVisible
                          ? t('navbar_hide_copilot')
                          : t('navbar_show_copilot')
                    }
                    data-tooltip={
                      rightLocked
                        ? t('navbar_copilot_active_on_agent')
                        : rightVisible
                          ? t('navbar_hide_copilot')
                          : t('navbar_show_copilot')
                    }
                  >
                    <SparklesIcon />
                  </button>
                </>
              )}

            </div>
          </>
        )}
      </nav>
    </>
  )
}

function WorkspaceTabStrip({
  projectId,
  onTabSelect,
  localQuestMode = false,
}: {
  projectId: string
  onTabSelect?: (tabId: string) => void
  localQuestMode?: boolean
}) {
  const { t } = useI18n('workspace')
  const { t: tCommon } = useI18n('common')
  const tabs = useTabsStore((s) => s.tabs)
  const activeTabId = useTabsStore((s) => s.activeTabId)
  const setActiveTab = useTabsStore((s) => s.setActiveTab)
  const closeTab = useTabsStore((s) => s.closeTab)
  const resetTabs = useTabsStore((s) => s.resetTabs)
  const stripRef = React.useRef<HTMLDivElement>(null)
  const tabRefs = React.useRef(new Map<string, HTMLDivElement>())
  const scrollAnimationRef = React.useRef<number | null>(null)
  const [showCloseAllConfirm, setShowCloseAllConfirm] = React.useState(false)

  const projectTabs = React.useMemo(
    () =>
      tabs.filter(
        (tab) => tabMatchesProject(tab, projectId) && (!localQuestMode || isQuestFriendlyTab(tab, projectId))
      ),
    [localQuestMode, projectId, tabs]
  )
  const activeProjectTabId = projectTabs.some((tab) => tab.id === activeTabId)
    ? activeTabId
    : null

  const registerTabRef = React.useCallback((tabId: string) => {
    return (node: HTMLDivElement | null) => {
      if (node) {
        tabRefs.current.set(tabId, node)
      } else {
        tabRefs.current.delete(tabId)
      }
    }
  }, [])

  const cancelScrollAnimation = React.useCallback(() => {
    if (scrollAnimationRef.current !== null) {
      cancelAnimationFrame(scrollAnimationRef.current)
      scrollAnimationRef.current = null
    }
  }, [])

  const animateScrollTo = React.useCallback(
    (targetLeft: number) => {
      const el = stripRef.current
      if (!el) return
      const startLeft = el.scrollLeft
      const delta = targetLeft - startLeft
      if (Math.abs(delta) < 1) return

      const durationMs = 360
      const startTime = performance.now()

      const step = (now: number) => {
        const progress = Math.min(1, (now - startTime) / durationMs)
        const eased = 1 - Math.pow(1 - progress, 3)
        el.scrollLeft = startLeft + delta * eased
        if (progress < 1) {
          scrollAnimationRef.current = requestAnimationFrame(step)
        } else {
          scrollAnimationRef.current = null
        }
      }

      cancelScrollAnimation()
      scrollAnimationRef.current = requestAnimationFrame(step)
    },
    [cancelScrollAnimation]
  )

  const scrollActiveTabIntoView = React.useCallback(
    (tabId: string) => {
      const el = stripRef.current
      const tabEl = tabRefs.current.get(tabId)
      if (!el || !tabEl) return

      const tabRect = tabEl.getBoundingClientRect()
      const containerRect = el.getBoundingClientRect()
      const padding = 24
      const isVisible =
        tabRect.left >= containerRect.left + padding &&
        tabRect.right <= containerRect.right - padding
      if (isVisible) return

      const tabCenter = tabRect.left + tabRect.width / 2
      const containerCenter = containerRect.left + containerRect.width / 2
      const delta = tabCenter - containerCenter
      const maxScrollLeft = el.scrollWidth - el.clientWidth
      const targetLeft = Math.max(0, Math.min(maxScrollLeft, el.scrollLeft + delta))

      // Ease-out scroll so the active tab glides into view.
      animateScrollTo(targetLeft)
    },
    [animateScrollTo]
  )

  React.useEffect(() => {
    if (!activeProjectTabId) return
    scrollActiveTabIntoView(activeProjectTabId)
  }, [activeProjectTabId, scrollActiveTabIntoView])

  React.useEffect(() => {
    const el = stripRef.current
    if (!el) return

    const handleWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return
      event.preventDefault()
      cancelScrollAnimation()
      el.scrollLeft += event.deltaY
    }

    el.addEventListener('wheel', handleWheel, { passive: false })
    return () => {
      el.removeEventListener('wheel', handleWheel)
      cancelScrollAnimation()
    }
  }, [cancelScrollAnimation])

  if (projectTabs.length === 0) {
    return <div className="chrome-tabstrip empty" />
  }

  const closeAllDescription = tabs.some((tab) => tab.isDirty)
    ? t('tabbar_close_all_dirty_desc')
    : t('tabbar_close_all_desc')
  const handleTabSelect = onTabSelect ?? setActiveTab

  return (
    <>
      <div className="group flex w-full min-w-0 items-stretch">
        <div
          ref={stripRef}
          className="chrome-tabstrip min-w-0 flex-1"
          role="tablist"
          aria-label={t('navbar_workspace')}
        >
          {projectTabs.map((tab) => {
            const isActive = tab.id === activeProjectTabId
            return (
              <div
                key={tab.id}
                role="tab"
                aria-selected={isActive}
                className={cn('chrome-tab', isActive && 'is-active')}
                ref={registerTabRef(tab.id)}
                onMouseDown={(e) => {
                  // avoid focusing buttons in navbar
                  e.preventDefault()
                }}
                onClick={() => handleTabSelect(tab.id)}
                title={tab.title}
              >
                <div className="chrome-tab-title">
                  <FileText className="h-3.5 w-3.5 shrink-0" />
                  <span className="truncate">{tab.title}</span>
                  {tab.isDirty && (
                    <span
                      className="chrome-tab-dirty"
                      aria-label={tCommon('unsaved', undefined, 'Unsaved')}
                    >
                      •
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  className="chrome-tab-close"
                  onClick={(e) => {
                    e.stopPropagation()
                    closeTab(tab.id)
                  }}
                  aria-label={t('tab_close')}
                  title={t('window_close')}
                >
                  ×
                </button>
              </div>
            )
          })}
        </div>

        <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center">
          <button
            type="button"
            className={cn(
              'h-6 w-6 rounded-md border border-black/60 text-black/70',
              'flex items-center justify-center bg-transparent hover:bg-black/5 hover:text-black',
              'opacity-0 pointer-events-none transition-opacity',
              'group-hover:opacity-100 group-hover:pointer-events-auto',
              'dark:border-white/40 dark:text-white/70 dark:hover:bg-white/10 dark:hover:text-white'
            )}
            onClick={() => setShowCloseAllConfirm(true)}
            aria-label={t('tabbar_close_all_tabs')}
            title={t('tabbar_close_all_tabs')}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <ConfirmModal
        open={showCloseAllConfirm}
        onClose={() => setShowCloseAllConfirm(false)}
        onConfirm={() => {
          resetTabs()
          setShowCloseAllConfirm(false)
        }}
        title={t('tabbar_close_all_title')}
        description={closeAllDescription}
        confirmText={t('tabbar_close_all_confirm')}
        cancelText={t('tabbar_cancel')}
        variant="danger"
      />
    </>
  )
}

// ============================================================================
// Left Panel (Dark Explorer)
// ============================================================================

function LeftPanel({
  width,
  projectId,
  onClose,
  readOnly,
  onEnterHome,
  onEnterLab,
  onExitHome,
  localQuestMode = false,
}: {
  width: number
  projectId: string
  onClose: () => void
  readOnly?: boolean
  onEnterHome?: () => void
  onEnterLab?: () => void
  onExitHome?: () => void
  localQuestMode?: boolean
}) {
  const { t } = useI18n('workspace')
  const { t: tCommon } = useI18n('common')
  const readOnlyMode = Boolean(readOnly)
  const { addToast } = useToast()
  const openTab = useTabsStore((state) => state.openTab)
  const { createFolder, upload, refresh, isLoading } = useFileTreeStore()
  const { openFileInTab, downloadFile, openNotebook } = useOpenFile()
  const fileInputRef = React.useRef<HTMLInputElement>(null)
  const explorerBodyRef = React.useRef<HTMLDivElement | null>(null)
  const [activeExplorer, setActiveExplorer] = React.useState<'files'>('files')
  const [hideDotfiles, setHideDotfiles] = React.useState(true)
  const [createFileOpen, setCreateFileOpen] = React.useState(false)
  const [isMenuOpen, setIsMenuOpen] = React.useState(true)
  const menuSectionId = React.useId()
  const activeTab = useActiveTab()
  const activeQuestWorkspaceView = React.useMemo(() => {
    if (!isQuestWorkspaceTab(activeTab, projectId)) {
      return null
    }
    return getQuestWorkspaceTabView(activeTab)
  }, [activeTab, projectId])

  React.useEffect(() => {
    if (activeExplorer !== 'files') {
      setActiveExplorer('files')
    }
  }, [activeExplorer])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    const handleRefresh = (event: Event) => {
      const detail = (event as CustomEvent).detail as ExplorerRefreshDetail | undefined
      if (!detail?.target) return
      if (detail.projectId && detail.projectId !== projectId) return
      detail.onComplete?.()
    }
    window.addEventListener(EXPLORER_REFRESH_EVENT, handleRefresh)
    return () => {
      window.removeEventListener(EXPLORER_REFRESH_EVENT, handleRefresh)
    }
  }, [projectId])

  const openPluginTab = React.useCallback(
    (pluginId: string, title: string, customData?: Record<string, unknown>) => {
      if (readOnlyMode) return
      onExitHome?.()
      openTab({
        pluginId,
        context: { type: 'custom', customData: { projectId, ...customData } },
        title,
      })
    },
    [onExitHome, openTab, projectId, readOnlyMode]
  )

  const openQuestWorkspaceTab = React.useCallback(
    (view: QuestWorkspaceView) => {
      onExitHome?.()
      openTab({
        pluginId: QUEST_WORKSPACE_PLUGIN_ID,
        context: buildQuestWorkspaceTabContext(projectId, view),
        title: getQuestWorkspaceTitle(view),
      })
    },
    [onExitHome, openTab, projectId]
  )

  const handleFileOpen = React.useCallback(
    async (file: FileNode) => {
      onExitHome?.()
      if (file.type === 'folder' && file.folderKind === 'latex') {
        openTab({
          pluginId: '@ds/plugin-latex',
          context: {
            type: 'custom',
            resourceId: file.id,
            resourceName: file.name,
            customData: {
              projectId,
              latexFolderId: file.id,
              mainFileId: file.latex?.mainFileId ?? null,
              readOnly: readOnlyMode,
            },
          },
          title: file.name,
        })
        return
      }
      if (file.type === 'notebook') {
        openNotebook(file.id, file.name, projectId, { readonly: readOnlyMode })
        return
      }
      await openFileInTab(file, {
        customData: {
          projectId,
          fileMeta: {
            updatedAt: file.updatedAt,
            sizeBytes: file.size,
            mimeType: file.mimeType,
          },
        },
      })
    },
    [
      onExitHome,
      openFileInTab,
      openNotebook,
      openTab,
      projectId,
      readOnlyMode,
    ]
  )

  const handleFileDownload = React.useCallback(
    async (file: FileNode) => {
      try {
        await downloadFile(file)
        addToast({
          type: 'success',
          title: t('toast_download_started'),
          description: file.name,
          duration: 1800,
        })
      } catch (error) {
        console.error('Download failed:', error)
        addToast({
          type: 'error',
          title: t('toast_download_failed'),
          description: tCommon('generic_try_again', undefined, 'Please try again.'),
        })
      }
    },
    [addToast, downloadFile, t, tCommon]
  )

  const handleNewFolder = React.useCallback(async () => {
    if (readOnlyMode) return
    try {
      await createFolder(null, t('command_new_folder_title'))
      addToast({ type: 'success', title: t('toast_folder_created'), duration: 1800 })
    } catch (error) {
      console.error('Failed to create folder:', error)
      addToast({
        type: 'error',
        title: t('toast_create_folder_failed'),
        description: tCommon('generic_try_again', undefined, 'Please try again.'),
      })
    }
  }, [addToast, createFolder, readOnlyMode, t, tCommon])

  const handleUploadClick = React.useCallback(() => {
    if (readOnlyMode) return
    fileInputRef.current?.click()
  }, [readOnlyMode])

  const handleFileSelect = React.useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      if (readOnlyMode) return
      const files = Array.from(e.target.files || [])
      if (files.length > 0) {
        try {
          await upload(null, files)
          addToast({
            type: 'success',
            title: t('toast_upload_started'),
            description: t('toast_upload_started_desc', { count: files.length }),
            duration: 2200,
          })
        } catch (error) {
          console.error('Upload failed:', error)
          addToast({
            type: 'error',
            title: t('toast_upload_failed'),
            description: tCommon('generic_try_again', undefined, 'Please try again.'),
          })
        }
      }
      e.target.value = ''
    },
    [addToast, readOnlyMode, t, tCommon, upload]
  )

  const handleRefresh = React.useCallback(async () => {
    try {
      await refresh()
      addToast({ type: 'success', title: t('toast_refreshed'), duration: 1200 })
    } catch (error) {
      console.error('Failed to refresh:', error)
      addToast({
        type: 'error',
        title: t('toast_refresh_failed'),
        description: tCommon('generic_try_again', undefined, 'Please try again.'),
      })
    }
  }, [addToast, refresh, t, tCommon])

  const isFilesView = activeExplorer === 'files'
  const disableExplorerActions = readOnlyMode
  const disableExplorerMutations = readOnlyMode || localQuestMode

  const handleExplorerNewFile = React.useCallback(() => {
    if (disableExplorerMutations) return
    setCreateFileOpen(true)
  }, [disableExplorerMutations])

  const handleExplorerNewFolder = React.useCallback(() => {
    if (disableExplorerMutations) return
    void handleNewFolder()
  }, [disableExplorerMutations, handleNewFolder])

  const handleExplorerUpload = React.useCallback(() => {
    if (disableExplorerMutations) return
    handleUploadClick()
  }, [disableExplorerMutations, handleUploadClick])

  const handleExplorerRefresh = React.useCallback(() => {
    if (disableExplorerActions) return
    void handleRefresh()
  }, [disableExplorerActions, handleRefresh])

  return (
    <div className="panel left-panel" style={{ width, minWidth: width }}>
      {/* Header */}
      <div className="panel-header flex flex-nowrap items-center">
        <div className="flex items-center gap-2">
          <div className="traffic-lights">
          <button
            type="button"
            className="traffic-light-close-button"
            onClick={onClose}
            title={t('leftpanel_close_explorer')}
            aria-label={t('leftpanel_close_explorer')}
          >
              <X className="h-3 w-3" />
            </button>
          </div>
          <span style={{ opacity: 0.8 }}>{t('leftpanel_explorer')}</span>
        </div>
        <div className="ml-auto flex items-center">
          <div
            className={cn(
              'flex items-center gap-0.5 whitespace-nowrap rounded-full border p-0.5 shadow-sm',
              'border-white/[0.16] bg-[var(--bg-panel-left)]'
            )}
            role="tablist"
            aria-label={t('explorer_views')}
          >
            <button
              type="button"
              className={cn(
                'inline-flex h-7 w-7 items-center justify-center rounded-full transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#9b8352]/40',
                isFilesView
                  ? 'bg-[#9b8352] text-white shadow-sm'
                  : 'text-[var(--text-muted-on-dark)] hover:bg-[#9b8352]/[0.18] hover:text-[var(--text-on-dark)]'
              )}
              onClick={() => setActiveExplorer('files')}
              role="tab"
              aria-selected={isFilesView}
              aria-label={t('explorer_files')}
              title={t('explorer_files')}
            >
              <FileText className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>

      {/* File Tree Section */}
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="flex items-center gap-3 px-4 py-2 border-b border-[var(--border-dark)]">
          <div className="ml-auto flex items-center gap-0.5">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={handleExplorerNewFile}
              disabled={disableExplorerMutations}
              className="h-7 w-7 rounded-lg p-0 text-[var(--text-muted-on-dark)] hover:bg-white/[0.08] hover:text-[var(--text-on-dark)]"
              title={
                disableExplorerMutations
                  ? readOnlyMode
                    ? t('leftpanel_view_only')
                    : localQuestMode
                      ? 'Create files from the document editor in local quest mode.'
                    : t('leftpanel_view_only')
                  : t('explorer_new_file')
              }
              aria-label={t('explorer_new_file')}
            >
              <FilePlus className="h-3.5 w-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={handleExplorerNewFolder}
              disabled={disableExplorerMutations}
              className="h-7 w-7 rounded-lg p-0 text-[var(--text-muted-on-dark)] hover:bg-white/[0.08] hover:text-[var(--text-on-dark)]"
              title={
                disableExplorerMutations
                  ? readOnlyMode
                    ? t('leftpanel_view_only')
                    : localQuestMode
                      ? 'Folder creation is not exposed in local quest mode.'
                    : t('leftpanel_view_only')
                  : t('explorer_new_folder')
              }
              aria-label={t('explorer_new_folder')}
            >
              <FolderPlus className="h-3.5 w-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={handleExplorerUpload}
              disabled={disableExplorerMutations}
              className="h-7 w-7 rounded-lg p-0 text-[var(--text-muted-on-dark)] hover:bg-white/[0.08] hover:text-[var(--text-on-dark)]"
              title={
                disableExplorerMutations
                  ? readOnlyMode
                    ? t('leftpanel_view_only')
                    : localQuestMode
                      ? 'Upload is disabled in local quest mode.'
                    : t('leftpanel_view_only')
                  : t('explorer_upload_files')
              }
              aria-label={t('explorer_upload_files')}
            >
              <Upload className="h-3.5 w-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => setHideDotfiles((prev) => !prev)}
              className="h-7 w-7 rounded-lg p-0 text-[var(--text-muted-on-dark)] hover:bg-white/[0.08] hover:text-[var(--text-on-dark)]"
              title={hideDotfiles ? t('explorer_show_dotfiles') : t('explorer_hide_dotfiles')}
              aria-label={hideDotfiles ? t('explorer_show_dotfiles') : t('explorer_hide_dotfiles')}
            >
              <DotfilesToggleIcon hidden={hideDotfiles} className="h-3.5 w-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={handleExplorerRefresh}
              disabled={disableExplorerActions || (isFilesView ? isLoading : false)}
              className="h-7 w-7 rounded-lg p-0 text-[var(--text-muted-on-dark)] hover:bg-white/[0.08] hover:text-[var(--text-on-dark)] disabled:opacity-50"
              title={
                disableExplorerActions
                  ? readOnlyMode
                    ? t('leftpanel_view_only')
                    : t('leftpanel_view_only')
                  : t('explorer_refresh')
              }
              aria-label={t('explorer_refresh')}
            >
              <RefreshCw
                className={cn('h-3.5 w-3.5 text-white', isFilesView && isLoading && 'animate-spin')}
              />
            </Button>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileSelect}
          />
        </div>

        <div ref={explorerBodyRef} className="flex-1 min-h-0 overflow-hidden flex flex-col">
          <div
            className={cn(
              'flex-1 min-h-0 overflow-hidden',
              isFilesView ? 'flex flex-col' : 'hidden'
            )}
            role="tabpanel"
            aria-hidden={!isFilesView}
          >
            <div className="flex-1 min-h-0 file-tree-dark flex flex-col">
              <FileTree
                projectId={projectId}
                onFileOpen={handleFileOpen}
                onFileDownload={handleFileDownload}
                className="flex-1 min-h-0"
                readOnly={readOnlyMode}
                hideDotfiles={hideDotfiles}
              />
            </div>
          </div>

        </div>
      </div>

      {!readOnlyMode && (
        <CreateFileDialog
          open={createFileOpen}
          onOpenChange={setCreateFileOpen}
          parentId={null}
          onCreated={(file) => {
            void handleFileOpen(file)
          }}
        />
      )}

      {!readOnlyMode && (
        <div className="shrink-0">
          <Separator className="mx-2 w-auto bg-[var(--border-dark)]" />
          {localQuestMode ? (
            <div className="p-2">
              <div className="px-1 pb-2 text-[10px] uppercase tracking-[0.18em] text-[var(--text-muted-on-dark)]">
                Workspace
              </div>
              <SidebarButton
                icon={<FolderOpen className="h-4 w-4" />}
                label={t('quest_workspace_canvas')}
                active={activeQuestWorkspaceView === 'canvas'}
                onClick={() => {
                  openQuestWorkspaceTab('canvas')
                }}
              />
              <SidebarButton
                icon={<FileText className="h-4 w-4" />}
                label={t('quest_workspace_details')}
                active={activeQuestWorkspaceView === 'details'}
                onClick={() => {
                  openQuestWorkspaceTab('details')
                }}
              />
              <SidebarButton
                icon={<Terminal className="h-4 w-4" />}
                label={t('quest_workspace_terminal')}
                active={activeQuestWorkspaceView === 'terminal'}
                onClick={() => {
                  openQuestWorkspaceTab('terminal')
                }}
              />
            </div>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setIsMenuOpen((prev) => !prev)}
                aria-expanded={isMenuOpen}
                aria-controls={menuSectionId}
                className={cn(
                  'flex w-full items-center justify-between px-2 py-1.5 text-[10px] uppercase tracking-wide',
                  'text-[var(--text-muted-on-dark)] transition-colors',
                  'hover:text-[var(--text-on-dark)]'
                )}
              >
                <span>{t('menu_label')}</span>
                <ChevronDown
                  className={cn('h-3 w-3 transition-transform', isMenuOpen ? 'rotate-0' : '-rotate-90')}
                />
              </button>
              <div id={menuSectionId} hidden={!isMenuOpen} aria-hidden={!isMenuOpen}>
                <div className="p-1.5 space-y-0.5">
                  <SidebarButton
                    icon={
                      <PngIcon
                        name="inverted/Search"
                        size={16}
                        className="h-4 w-4"
                        fallback={<Search className="h-4 w-4" />}
                      />
                    }
                    label={t('leftpanel_search')}
                    onClick={() => openPluginTab(BUILTIN_PLUGINS.SEARCH, t('plugin_search_title'))}
                  />
                  <SidebarButton
                    icon={
                      <PngIcon
                        name="inverted/BarChart3"
                        size={16}
                        className="h-4 w-4"
                        fallback={<BarChart3 className="h-4 w-4" />}
                      />
                    }
                    label={t('leftpanel_analysis')}
                    onClick={() => openPluginTab('@ds/plugin-analysis', t('leftpanel_analysis'))}
                  />
                  <SidebarButton
                    icon={
                      <PngIcon
                        name="inverted/Puzzle"
                        size={16}
                        className="h-4 w-4"
                        fallback={<Puzzle className="h-4 w-4" />}
                      />
                    }
                    label={t('leftpanel_plugins')}
                    onClick={() => openPluginTab('@ds/plugin-marketplace', t('plugin_marketplace_title'))}
                  />
                </div>

                <Separator className="mx-2 w-auto bg-[var(--border-dark)]" />

                <div className="p-1.5">
                  <SidebarButton
                    icon={<Settings className="h-4 w-4" />}
                    label={t('leftpanel_settings')}
                    onClick={openSettings}
                  />
                  <SidebarButton
                    icon={
                      <PngIcon
                        name="inverted/SparklesIcon"
                        alt={t('leftpanel_agent')}
                        size={16}
                        className="h-4 w-4"
                        fallback={<SparklesIcon className="h-4 w-4" />}
                      />
                    }
                    label={t('leftpanel_agent')}
                    onClick={() => onEnterHome?.()}
                  />
                  <SidebarButton
                    icon={<FlaskConical className="h-4 w-4" />}
                    label={t('leftpanel_home')}
                    onClick={() => {
                      onEnterLab?.()
                      openPluginTab(BUILTIN_PLUGINS.LAB, t('plugin_lab_home_title'), { readOnly: readOnlyMode })
                    }}
                  />
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function SidebarButton({
  icon,
  label,
  onClick,
  active,
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  active?: boolean
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex items-center gap-2 w-full px-2 py-1.5 rounded-lg',
        'text-[var(--text-muted-on-dark)] text-xs font-medium',
        'transition-colors duration-150',
        'hover:bg-white/[0.06] hover:text-[var(--text-on-dark)]',
        active && 'bg-white/[0.08] text-[var(--text-on-dark)]'
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}

// ============================================================================
// Center Panel (Main Content Area)
// ============================================================================

function CenterPanel({
  projectId,
  readOnly,
  safePaddingLeft,
  safePaddingRight,
  overlay,
  onExitHome,
  localQuestMode = false,
}: {
  projectId: string
  readOnly?: boolean
  safePaddingLeft: number
  safePaddingRight: number
  overlay?: React.ReactNode
  onExitHome?: () => void
  localQuestMode?: boolean
}) {
  const { t } = useI18n('workspace')
  const tabs = useTabsStore((s) => s.tabs)
  const tabsHydrated = useTabsStore((s) => s.hasHydrated)
  const openTab = useTabsStore((s) => s.openTab)
  const activeTab = useActiveTab()
  const setActiveTab = useTabsStore((s) => s.setActiveTab)
  const updateTabPlugin = useTabsStore((s) => s.updateTabPlugin)
  const projectTabs = React.useMemo(
    () =>
      tabs.filter(
        (tab) => tabMatchesProject(tab, projectId) && (!localQuestMode || isQuestFriendlyTab(tab, projectId))
      ),
    [localQuestMode, projectId, tabs]
  )
  const activeProjectTab =
    activeTab &&
    tabMatchesProject(activeTab, projectId) &&
    (!localQuestMode || isQuestFriendlyTab(activeTab, projectId))
      ? activeTab
      : null
  const openQuestWorkspaceTab = React.useCallback(
    (view: QuestWorkspaceView) => {
      onExitHome?.()
      openTab({
        pluginId: QUEST_WORKSPACE_PLUGIN_ID,
        context: buildQuestWorkspaceTabContext(projectId, view),
        title: getQuestWorkspaceTitle(view),
      })
    },
    [onExitHome, openTab, projectId]
  )
  const resolvedTab = activeProjectTab ?? projectTabs[0] ?? null
  const resolvedQuestWorkspaceView = React.useMemo(() => {
    if (!isQuestWorkspaceTab(resolvedTab, projectId)) {
      return null
    }
    return getQuestWorkspaceTabView(resolvedTab)
  }, [projectId, resolvedTab])
  const isNotebook = resolvedTab?.pluginId === BUILTIN_PLUGINS.NOTEBOOK
  const isFullBleedCanvas = isNotebook
  const activeTabIdForProject = resolvedTab?.id ?? null
  const [mountedTabIds, setMountedTabIds] = React.useState<Set<string>>(
    () => (activeTabIdForProject ? new Set([activeTabIdForProject]) : new Set())
  )
  const [tabSwitching, setTabSwitching] = React.useState(false)
  const tabSwitchTimerRef = React.useRef<number | null>(null)
  const tabSwitchRafRef = React.useRef<number | null>(null)
  const didTabSwitchRef = React.useRef(false)

  React.useEffect(() => {
    if (projectTabs.length === 0) return
    projectTabs.forEach((tab) => {
      if (tab.pluginId !== BUILTIN_PLUGINS.PDF_VIEWER) return
      if (!isMarkdownContext(tab.context.resourceName, tab.context.mimeType, tab.context.customData?.docKind)) {
        return
      }
      updateTabPlugin(tab.id, BUILTIN_PLUGINS.NOTEBOOK, {
        ...tab.context,
        mimeType: tab.context.mimeType ?? 'text/markdown',
        customData: {
          ...(tab.context.customData || {}),
          docKind: 'markdown',
        },
      })
    })
  }, [projectTabs, updateTabPlugin])

  React.useEffect(() => {
    if (!resolvedTab || activeProjectTab) return
    setActiveTab(resolvedTab.id)
  }, [activeProjectTab, resolvedTab, setActiveTab])

  React.useEffect(() => {
    if (!activeTabIdForProject) return
    if (!didTabSwitchRef.current) {
      didTabSwitchRef.current = true
      return
    }
    if (tabSwitchTimerRef.current) {
      window.clearTimeout(tabSwitchTimerRef.current)
    }
    if (tabSwitchRafRef.current) {
      window.cancelAnimationFrame(tabSwitchRafRef.current)
    }
    setTabSwitching(false)
    tabSwitchRafRef.current = window.requestAnimationFrame(() => {
      setTabSwitching(true)
      tabSwitchTimerRef.current = window.setTimeout(() => {
        setTabSwitching(false)
        tabSwitchTimerRef.current = null
      }, TAB_SWITCH_MS)
    })
    return () => {
      if (tabSwitchTimerRef.current) window.clearTimeout(tabSwitchTimerRef.current)
      if (tabSwitchRafRef.current) window.cancelAnimationFrame(tabSwitchRafRef.current)
      tabSwitchTimerRef.current = null
      tabSwitchRafRef.current = null
    }
  }, [activeTabIdForProject])

  React.useEffect(() => {
    if (!activeTabIdForProject) return
    setMountedTabIds((prev) => {
      if (prev.has(activeTabIdForProject)) return prev
      const next = new Set(prev)
      next.add(activeTabIdForProject)
      return next
    })
  }, [activeTabIdForProject])

  React.useEffect(() => {
    setMountedTabIds((prev) => {
      if (prev.size === 0) return prev
      const currentIds = new Set(projectTabs.map((tab) => tab.id))
      let changed = false
      const next = new Set<string>()
      for (const id of prev) {
        if (currentIds.has(id)) {
          next.add(id)
        } else {
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [projectTabs])

  if (!tabsHydrated && projectTabs.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t('layout_loading')}
      </div>
    )
  }

  if (projectTabs.length === 0) {
    if (localQuestMode) {
      return (
        <div className="panel center-panel morandi-glow ds-stage" style={{ flex: 1 }}>
          <div
            className="ds-stage-safe flex h-full items-center justify-center text-sm text-muted-foreground"
            style={{ paddingLeft: safePaddingLeft, paddingRight: safePaddingRight }}
          >
            {t('layout_loading')}
          </div>
          {overlay}
        </div>
      )
    }
    return (
      <EmptyWorkspace
        projectId={projectId}
        readOnly={readOnly}
        safePaddingLeft={safePaddingLeft}
        safePaddingRight={safePaddingRight}
        overlay={overlay}
        onExitHome={onExitHome}
      />
    )
  }

  if (localQuestMode && resolvedQuestWorkspaceView) {
    return (
      <QuestWorkspaceSurface
        questId={projectId}
        safePaddingLeft={safePaddingLeft}
        safePaddingRight={safePaddingRight}
        overlay={overlay}
        view={resolvedQuestWorkspaceView}
        onViewChange={openQuestWorkspaceTab}
      />
    )
  }

  return (
    <div className="panel center-panel morandi-glow ds-stage" style={{ flex: 1 }}>
      <div
        className={cn('ds-stage-safe', tabSwitching && 'ds-stage-switch')}
        style={{ paddingLeft: safePaddingLeft, paddingRight: safePaddingRight }}
      >
        {/* Plugin Render Area */}
        <div className={cn('canvas-content', isFullBleedCanvas && 'ds-canvas-fullbleed')}>
          {resolvedTab ? (
            <>
              {projectTabs.map((tab) => {
                const isActive = tab.id === activeTabIdForProject
                const shouldRender = isActive || mountedTabIds.has(tab.id)
                if (!shouldRender) return null
                const shouldRedirectMarkdown =
                  tab.pluginId === BUILTIN_PLUGINS.PDF_VIEWER &&
                  isMarkdownContext(
                    tab.context.resourceName,
                    tab.context.mimeType,
                    tab.context.customData?.docKind
                  )
                return (
                  <div key={tab.id} className={cn(isActive ? 'contents' : 'hidden')}>
                    {shouldRedirectMarkdown ? (
                      <div className="flex h-full items-center justify-center text-[var(--text-muted)]">
                        <div className="text-sm">{t('content_switching_markdown')}</div>
                      </div>
                    ) : (
                      <PluginRenderer
                        pluginId={tab.pluginId}
                        context={tab.context}
                        tabId={tab.id}
                        projectId={projectId}
                      />
                    )}
                  </div>
                )
              })}
            </>
          ) : (
            <div className="h-full flex items-center justify-center text-[var(--text-muted)]">
              Select a tab to view its content
            </div>
          )}
        </div>
      </div>
      {overlay}
    </div>
  )
}

function ActionCard({
  title,
  description,
  icon,
  onClick,
  spotlightColor = 'rgba(143, 163, 184, 0.18)',
}: {
  title: string
  description: string
  icon: React.ReactNode
  onClick: () => void
  spotlightColor?: string
}) {
  return (
    <SpotlightCard className="rounded-2xl" spotlightColor={spotlightColor}>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'group w-full text-left rounded-2xl border border-[var(--border-light)]',
          'bg-white/80 px-3.5 py-3 shadow-[0_1px_2px_rgba(0,0,0,0.04)]',
          'transition-all duration-300',
          'hover:shadow-[0_10px_30px_rgba(0,0,0,0.08)] hover:border-black/10',
          'motion-safe:hover:-translate-y-0.5 motion-safe:active:translate-y-0',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-black/10',
          'dark:bg-white/[0.04] dark:hover:border-white/[0.15] dark:focus-visible:ring-white/20'
        )}
      >
        <div className="flex items-center justify-between">
          <div className="font-medium text-[var(--text-main)]">{title}</div>
          {icon}
        </div>
        <div className="mt-0.5 text-xs text-[var(--text-muted)]">{description}</div>
      </button>
    </SpotlightCard>
  )
}

function EmptyWorkspace({
  projectId,
  readOnly,
  safePaddingLeft,
  safePaddingRight,
  overlay,
  onExitHome,
}: {
  projectId: string
  readOnly?: boolean
  safePaddingLeft: number
  safePaddingRight: number
  overlay?: React.ReactNode
  onExitHome?: () => void
}) {
  const { t } = useI18n('workspace')
  const { t: tCommon } = useI18n('common')
  const readOnlyMode = Boolean(readOnly)
  const openTab = useTabsStore((state) => state.openTab)
  const { upload } = useFileTreeStore()
  const { openFileInTab } = useOpenFile()
  const { addToast } = useToast()
  const fileInputRef = React.useRef<HTMLInputElement>(null)
  const [createFileOpen, setCreateFileOpen] = React.useState(false)

  const handleUploadClick = React.useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleNewNotebook = React.useCallback(() => {
    onExitHome?.()
    ;(async () => {
      const notebook = await createNotebook(projectId, {
        title: t('command_new_notebook_title'),
        collaborationEnabled: true,
      })
      openTab({
        pluginId: BUILTIN_PLUGINS.NOTEBOOK,
        context: {
          type: 'notebook',
          resourceId: notebook.id,
          resourceName: notebook.title,
          customData: { projectId },
        },
        title: notebook.title,
      })
    })().catch((e) => {
      console.error('[WorkspaceLayout] Failed to create notebook:', e)
    })
  }, [onExitHome, openTab, projectId])

  const handleOpenAnalysis = React.useCallback(() => {
    onExitHome?.()
    openTab({
      pluginId: '@ds/plugin-analysis',
      context: { type: 'custom', customData: { projectId } },
      title: t('leftpanel_analysis'),
    })
  }, [onExitHome, openTab, projectId, t])

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || [])
    if (files.length > 0) {
      try {
        await upload(null, files)
        addToast({
          type: 'success',
          title: t('toast_upload_started'),
          description: t('toast_upload_started_desc', { count: files.length }),
          duration: 2200,
        })
      } catch (error) {
        console.error('[WorkspaceLayout] Upload failed:', error)
        addToast({
          type: 'error',
          title: t('toast_upload_failed'),
          description: tCommon('generic_try_again', undefined, 'Please try again.'),
        })
      }
    }
    e.target.value = ''
  }

  if (readOnlyMode) {
    return (
      <div className="panel center-panel morandi-glow ds-stage" style={{ flex: 1 }}>
        <div className="ds-stage-safe" style={{ paddingLeft: safePaddingLeft, paddingRight: safePaddingRight }}>
          <div className="h-full flex items-center justify-center p-7">
            <FadeContent duration={0.45} y={12}>
              <div className="max-w-md text-center space-y-2">
                <div className="text-lg font-semibold text-[var(--text-main)]">
                  {t('workspace_view_only_title')}
                </div>
                <div className="text-sm text-[var(--text-muted)]">
                  {t('workspace_view_only_desc')}
                </div>
              </div>
            </FadeContent>
          </div>
        </div>
        {overlay}
      </div>
    )
  }

  return (
    <div className="panel center-panel morandi-glow ds-stage" style={{ flex: 1 }}>
      <div className="ds-stage-safe" style={{ paddingLeft: safePaddingLeft, paddingRight: safePaddingRight }}>
        <div className="h-full flex items-center justify-center p-7">
          <div className="w-full max-w-2xl">
            {/* Header */}
            <FadeContent duration={0.5} y={16}>
              <div className="text-center mb-7">
                <div className="mx-auto mb-4 w-fit">
                  <Icon3D name="sparkle" size="xl" className="opacity-95" />
                </div>
                <h2 className="text-2xl font-semibold text-[var(--text-main)] tracking-tight">
                  {t('workspace_home_title')}
                </h2>
                <p className="mt-1.5 text-sm text-[var(--text-muted)]">
                  {t('workspace_home_desc')}
                </p>
              </div>
            </FadeContent>

            {/* Action Cards */}
            <FadeContent delay={0.08} duration={0.5} y={16}>
              <div className="grid grid-cols-2 gap-3">
                <ActionCard
                  title={t('workspace_card_new_file_title')}
                  description={t('workspace_card_new_file_desc')}
                  onClick={() => setCreateFileOpen(true)}
                  spotlightColor="rgba(143, 163, 184, 0.2)"
                  icon={
                    <FilePlus className="h-4 w-4 text-[var(--text-muted)] group-hover:text-[var(--text-main)] transition-colors" />
                  }
                />

                <ActionCard
                  title={t('workspace_card_new_notebook_title')}
                  description={t('workspace_card_new_notebook_desc')}
                  onClick={handleNewNotebook}
                  spotlightColor="rgba(126, 154, 191, 0.2)"
                  icon={
                    <PngIcon
                      name="BookOpen"
                      size={16}
                      className="h-4 w-4"
                      fallback={
                        <BookOpen className="h-4 w-4 text-[var(--text-muted)] group-hover:text-[var(--text-main)] transition-colors" />
                      }
                    />
                  }
                />

                <ActionCard
                  title={t('workspace_card_upload_title')}
                  description={t('workspace_card_upload_desc')}
                  onClick={handleUploadClick}
                  spotlightColor="rgba(201, 176, 132, 0.22)"
                  icon={
                    <Upload className="h-4 w-4 text-[var(--text-muted)] group-hover:text-[var(--text-main)] transition-colors" />
                  }
                />

                <ActionCard
                  title={t('workspace_card_copilot_title')}
                  description={t('workspace_card_copilot_desc')}
                  onClick={handleOpenAnalysis}
                  spotlightColor="rgba(138, 169, 129, 0.24)"
                  icon={
                    <Sparkles className="h-4 w-4 text-[var(--brand)] opacity-80 group-hover:opacity-100 transition-opacity" />
                  }
                />
              </div>
            </FadeContent>

            {/* Hints */}
            <FadeContent delay={0.16} duration={0.45} y={12}>
              <div className="mt-7 flex flex-wrap items-center justify-center gap-2 text-xs text-[var(--text-muted)]">
                <span className="inline-flex items-center gap-2">
                  <kbd className="px-1.5 py-0.5 bg-black/[0.03] border border-black/[0.06] rounded text-[11px] font-mono dark:bg-white/[0.06] dark:border-white/[0.10]">
                    ⌘K
                  </kbd>
                  {t('workspace_shortcut')}
                </span>
                <span className="text-[var(--border-light)]">•</span>
                <span>{t('workspace_drag_drop')}</span>
              </div>
            </FadeContent>

            <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleFileUpload} />

            <CreateFileDialog
              open={createFileOpen}
              onOpenChange={setCreateFileOpen}
              parentId={null}
              onCreated={(file) => {
                onExitHome?.()
                if (file.type === 'folder' && file.folderKind === 'latex') {
                  openTab({
                    pluginId: '@ds/plugin-latex',
                    context: {
                      type: 'custom',
                      resourceId: file.id,
                      resourceName: file.name,
                      customData: {
                        projectId,
                        latexFolderId: file.id,
                        mainFileId: file.latex?.mainFileId ?? null,
                        readOnly: readOnlyMode,
                      },
                    },
                    title: file.name,
                  })
                  return
                }
                void openFileInTab(file, {
                  customData: {
                    projectId,
                    fileMeta: {
                      updatedAt: file.updatedAt,
                      sizeBytes: file.size,
                      mimeType: file.mimeType,
                    },
                  },
                })
              }}
            />
          </div>
        </div>
      </div>
      {overlay}
    </div>
  )
}

// ============================================================================
// Main Layout
// ============================================================================

export function WorkspaceLayout({
  projectId,
  projectName,
  projectSource = null,
  readOnly = false,
  isSharedView = false,
}: WorkspaceLayoutProps) {
  const { t } = useI18n('workspace')
  const { t: tCommon } = useI18n('common')
  const router = useRouter()
  const readOnlyMode = Boolean(readOnly)
  const isLocalQuestProject = projectSource === 'quest'
  const workspaceProjectTitle = projectName ?? (projectId ? `Project ${projectId}` : 'Project')
  const { user } = useAuthStore()
  const { addToast } = useToast()
  const [tokenDialogOpen, setTokenDialogOpen] = React.useState(false)
  const [tokenLoading, setTokenLoading] = React.useState(false)
  const [tokenError, setTokenError] = React.useState('')
  const [myToken, setMyToken] = React.useState('')
  const [tokenRefreshLoading, setTokenRefreshLoading] = React.useState(false)
  const [tokenRefreshError, setTokenRefreshError] = React.useState('')
  const tabsHydrated = useTabsStore((state) => state.hasHydrated)
  const activeTab = useActiveTab()
  const shareReadOnly = isSharedView || isShareViewForProject(projectId)
  const activeLabContextSessionId = React.useMemo(
    () => getLabContextSessionId(activeTab, projectId),
    [activeTab, projectId]
  )
  const isLabContextActive = Boolean(activeLabContextSessionId)
  const copilotSurfaceStorageKey = `ds:project:${projectId}:copilot-surface`
  const defaultCopilotSurface: CopilotSurfaceMode =
    isLocalQuestProject
      ? 'agent'
      : (activeTab?.pluginId === BUILTIN_PLUGINS.LAB && tabMatchesProject(activeTab, projectId)) ||
          isLabContextActive
      ? 'lab'
      : 'agent'
  const [copilotSurface, setCopilotSurface] = React.useState<CopilotSurfaceMode>(() => {
    if (typeof window === 'undefined') return defaultCopilotSurface
    if (isLocalQuestProject) return 'agent'
    const stored = window.localStorage.getItem(copilotSurfaceStorageKey)
    if (stored === 'lab' || stored === 'agent') return stored
    return defaultCopilotSurface
  })
  const isLabTab = copilotSurface === 'lab'
  const labDataEnabled = Boolean(projectId && isLabTab && !isLocalQuestProject)
  const labStaleTime = 30000
  const cliServers = useCliStore((state) => state.servers)
  const storedCliServerId = useChatSessionStore((state) =>
    projectId ? state.cliServerIdsByProject[projectId] ?? null : null
  )
  const clearLabSelections = useLabCopilotStore((state) => state.clearSelections)

  React.useEffect(() => {
    clearLabSelections()
  }, [clearLabSelections, projectId])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    if (isLocalQuestProject) {
      setCopilotSurface('agent')
      return
    }
    const stored = window.localStorage.getItem(copilotSurfaceStorageKey)
    if (stored === 'lab' || stored === 'agent') {
      setCopilotSurface(stored)
      return
    }
    setCopilotSurface(defaultCopilotSurface)
  }, [copilotSurfaceStorageKey, defaultCopilotSurface, isLocalQuestProject])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    if (isLocalQuestProject) return
    window.localStorage.setItem(copilotSurfaceStorageKey, copilotSurface)
  }, [copilotSurface, copilotSurfaceStorageKey, isLocalQuestProject])

  const templatesQuery = useQuery({
    queryKey: ['lab-templates', projectId],
    queryFn: () => listLabTemplates(projectId),
    enabled: labDataEnabled && !shareReadOnly && !isLocalQuestProject,
    staleTime: labStaleTime,
  })
  const agentsQuery = useQuery({
    queryKey: ['lab-agents', projectId],
    queryFn: () => listLabAgents(projectId, { silent: true }),
    enabled: labDataEnabled && !shareReadOnly && !isLocalQuestProject,
    staleTime: labStaleTime,
  })
  const questsQuery = useQuery({
    queryKey: ['lab-quests', projectId],
    queryFn: () => listLabQuests(projectId, { silent: true }),
    enabled: labDataEnabled && !shareReadOnly && !isLocalQuestProject,
    staleTime: labStaleTime,
  })

  const templates = templatesQuery.data?.items ?? []
  const agents = agentsQuery.data?.items ?? []
  const quests = questsQuery.data?.items ?? []
  const onlineCliServers = React.useMemo(
    () => cliServers.filter((server) => server.status !== 'offline' && server.status !== 'error'),
    [cliServers]
  )
  const cliStatus: 'online' | 'offline' | 'unbound' =
    cliServers.length === 0 ? 'unbound' : onlineCliServers.length > 0 ? 'online' : 'offline'
  const boundCliServer = React.useMemo(() => {
    if (!storedCliServerId) return null
    return cliServers.find((server) => server.id === storedCliServerId) ?? null
  }, [cliServers, storedCliServerId])
  const boundCliServerOnline = Boolean(
    boundCliServer && boundCliServer.status !== 'offline' && boundCliServer.status !== 'error'
  )
  const effectiveCliStatus: 'online' | 'offline' | 'unbound' =
    cliStatus === 'online' && !boundCliServerOnline ? 'unbound' : cliStatus
  const labCopilotReadOnly = readOnlyMode || shareReadOnly || effectiveCliStatus !== 'online'
  const leftStorageKey = `ds:project:${projectId}:left-panel`
  const navbarStorageKey = `ds:project:${projectId}:navbar-collapsed`
  const homeStorageKey = `ds:project:${projectId}:home-mode`
  const [homeMode, setHomeMode] = React.useState(() => {
    if (typeof window === 'undefined') return false
    if (projectSource === 'quest') return false
    return window.localStorage.getItem(homeStorageKey) === '1'
  })
  const [homeVisible, setHomeVisible] = React.useState(homeMode)
  const [homeEntering, setHomeEntering] = React.useState(false)
  const homeEnteringTimerRef = React.useRef<number | null>(null)
  const lastActiveTabIdRef = React.useRef<string | null>(null)
  const homeRestoreRef = React.useRef(homeMode)
  const tabs = useTabsStore((s) => s.tabs)
  const setActiveTab = useTabsStore((s) => s.setActiveTab)
  const openTab = useTabsStore((s) => s.openTab)
  const resetTabs = useTabsStore((s) => s.resetTabs)
  const createFolder = useFileTreeStore((s) => s.createFolder)
  const loadFiles = useFileTreeStore((s) => s.loadFiles)
  const upload = useFileTreeStore((s) => s.upload)
  const { openFileInTab } = useOpenFile()
  const [leftWidth, setLeftWidth] = React.useState(280)
  const [showLeft, setShowLeft] = React.useState(() => {
    if (typeof window === 'undefined') return true
    const stored = window.localStorage.getItem(leftStorageKey)
    return stored ? stored === '1' : true
  })
  const [navbarCollapsed, setNavbarCollapsed] = React.useState(() => {
    if (typeof window === 'undefined') return false
    const stored = window.localStorage.getItem(navbarStorageKey)
    return stored === '1'
  })
  const [copilotPrefill, setCopilotPrefill] = React.useState<CopilotPrefill | null>(null)
  const copilotActionsRef = React.useRef<AiManusChatActions | null>(null)
  const handleLabClearChat = React.useCallback(() => {
    const actions = copilotActionsRef.current
    if (!actions?.clearThread) return
    actions.clearThread()
    window.setTimeout(() => actions.focusComposer(), 0)
  }, [])
  const pendingCopilotOpenRef = React.useRef(false)
  const [commandOpen, setCommandOpen] = React.useState(false)
  const [createFileOpen, setCreateFileOpen] = React.useState(false)
  const [createLatexOpen, setCreateLatexOpen] = React.useState(false)
  const [shareDialogOpen, setShareDialogOpen] = React.useState(false)
  const [canShare, setCanShare] = React.useState(false)
  const [canCopy, setCanCopy] = React.useState(false)
  const [shareCopyAllowed, setShareCopyAllowed] = React.useState(false)
  const [sharePermissionReady, setSharePermissionReady] = React.useState(false)
  const copilotDock = useCopilotDockState(projectId, { defaultOpen: !readOnlyMode })
  const [scrollbarSide, setScrollbarSide] = React.useState<ScrollbarSide>(() => {
    if (readOnlyMode || !copilotDock.state.open) return 'right'
    return copilotDock.state.side === 'right' ? 'left' : 'right'
  })
  const [scrollbarFadePhase, setScrollbarFadePhase] = React.useState<'idle' | 'out' | 'in'>('idle')
  const [scrollbarFadeMs, setScrollbarFadeMs] = React.useState(180)
  const scrollbarTimersRef = React.useRef<{ out: number | null; done: number | null } | null>(null)
  const [stageEl, setStageEl] = React.useState<HTMLDivElement | null>(null)
  const stageRef = React.useCallback((node: HTMLDivElement | null) => {
    setStageEl(node)
  }, [])
  const [stageWidth, setStageWidth] = React.useState(0)
  const [entranceStage, setEntranceStage] = React.useState<'hold' | 'from' | 'to' | 'done'>('done')
  const [navbarMotion, setNavbarMotion] = React.useState<'collapse' | 'expand' | 'none'>('none')
  const toggleNavbarCollapsed = React.useCallback(() => {
    setNavbarCollapsed((prev) => {
      const next = !prev
      setNavbarMotion(next ? 'collapse' : 'expand')
      return next
    })
  }, [])

  React.useEffect(() => {
    if (homeMode && copilotSurface !== 'agent') {
      setCopilotSurface('agent')
    }
  }, [copilotSurface, homeMode])

  React.useEffect(() => {
    setSharePermissionReady(false)
    if (!projectId || isSharedView) return
    return scheduleIdle(() => setSharePermissionReady(true), 1400)
  }, [isSharedView, projectId])

  const clearHomeEnteringTimer = React.useCallback(() => {
    if (homeEnteringTimerRef.current === null) return
    window.clearTimeout(homeEnteringTimerRef.current)
    homeEnteringTimerRef.current = null
  }, [])

  const enterHome = React.useCallback(() => {
    lastActiveTabIdRef.current = activeTab?.id ?? null
    homeRestoreRef.current = false
    clearHomeEnteringTimer()
    setHomeEntering(true)
    homeEnteringTimerRef.current = window.setTimeout(() => {
      homeEnteringTimerRef.current = null
      setHomeEntering(false)
    }, HOME_SWITCH_MS)
    setHomeVisible(true)
    setHomeMode(true)
    setCopilotSurface('agent')
  }, [activeTab?.id, clearHomeEnteringTimer, setCopilotSurface])

  const enterLab = React.useCallback(() => {
    setCopilotSurface('lab')
    if (!readOnlyMode) {
      copilotDock.setOpen(true)
    }
  }, [copilotDock, readOnlyMode, setCopilotSurface])

  const exitHome = React.useCallback(() => {
    clearHomeEnteringTimer()
    setHomeEntering(false)
    homeRestoreRef.current = false
    if (!homeMode) return
    setHomeMode(false)
    if (!readOnlyMode) {
      copilotDock.setOpen(true)
    }
  }, [clearHomeEnteringTimer, copilotDock, homeMode, readOnlyMode])

  const handleTabSelect = React.useCallback(
    (tabId: string) => {
      exitHome()
      setActiveTab(tabId)
    },
    [exitHome, setActiveTab]
  )

  React.useEffect(() => {
    if (homeMode) return
    lastActiveTabIdRef.current = activeTab?.id ?? null
  }, [activeTab?.id, homeMode])

  React.useEffect(() => {
    if (!homeMode) return
    const activeTabId = activeTab?.id ?? null
    if (!activeTabId) return
    if (homeRestoreRef.current) {
      lastActiveTabIdRef.current = activeTabId
      homeRestoreRef.current = false
      return
    }
    if (activeTabId !== lastActiveTabIdRef.current) {
      exitHome()
    }
  }, [activeTab?.id, exitHome, homeMode])

  React.useEffect(() => {
    if (homeMode) {
      setHomeVisible(true)
      return
    }
    if (!homeVisible) return
    const timer = window.setTimeout(() => {
      setHomeVisible(false)
    }, HOME_SWITCH_MS)
    return () => window.clearTimeout(timer)
  }, [homeMode, homeVisible])

  React.useEffect(() => clearHomeEnteringTimer, [clearHomeEnteringTimer])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    if (isLocalQuestProject) {
      homeRestoreRef.current = false
      setHomeVisible(false)
      setHomeMode(false)
      return
    }
    const stored = window.localStorage.getItem(homeStorageKey)
    const shouldShowHome = stored === '1'
    homeRestoreRef.current = shouldShowHome
    setHomeMode(shouldShowHome)
    if (shouldShowHome) {
      setHomeVisible(true)
    }
  }, [homeStorageKey, isLocalQuestProject])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(homeStorageKey, homeMode ? '1' : '0')
  }, [homeMode, homeStorageKey])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    const stored = window.localStorage.getItem(leftStorageKey)
    setShowLeft(stored ? stored === '1' : true)
  }, [leftStorageKey])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    const handleWorkspaceLeftVisibility = (event: Event) => {
      const detail = (event as CustomEvent<WorkspaceLeftVisibilityDetail>).detail
      if (!detail || detail.projectId !== projectId || typeof detail.visible !== 'boolean') return
      setShowLeft(detail.visible)
    }
    window.addEventListener(WORKSPACE_LEFT_VISIBILITY_EVENT, handleWorkspaceLeftVisibility as EventListener)
    return () =>
      window.removeEventListener(
        WORKSPACE_LEFT_VISIBILITY_EVENT,
        handleWorkspaceLeftVisibility as EventListener
      )
  }, [projectId])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(leftStorageKey, showLeft ? '1' : '0')
  }, [leftStorageKey, showLeft])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    const stored = window.localStorage.getItem(navbarStorageKey)
    setNavbarCollapsed(stored === '1')
  }, [navbarStorageKey])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(navbarStorageKey, navbarCollapsed ? '1' : '0')
  }, [navbarCollapsed, navbarStorageKey])

  React.useEffect(() => {
    if (navbarMotion === 'none') return
    const t = window.setTimeout(() => setNavbarMotion('none'), 700)
    return () => window.clearTimeout(t)
  }, [navbarMotion])

  React.useEffect(() => {
    let cancelled = false
    if (!sharePermissionReady || isSharedView || !projectId || !user?.id) {
      setCanShare(false)
      setCanCopy(false)
      return
    }
    ;(async () => {
      try {
        const access = await checkProjectAccess(projectId)
        if (cancelled) return
        const hasAccess = Boolean(access?.has_access)
        setCanCopy(hasAccess)
        setCanShare(Boolean(hasAccess && (access.role === 'owner' || access.role === 'admin')))
      } catch {
        if (cancelled) return
        setCanShare(false)
        setCanCopy(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isSharedView, projectId, sharePermissionReady, user?.id])

  React.useEffect(() => {
    try {
      const entry = getSharedProjects().find((p) => p.projectId === projectId)
      setShareCopyAllowed(Boolean(entry?.permission === 'view' && entry.allowCopy))
    } catch {
      setShareCopyAllowed(false)
    }
  }, [projectId])

  const isHomeSurface = homeMode
  const shouldShowCopilot =
    !readOnlyMode && copilotDock.state.open && (!isHomeSurface || homeEntering)
  const initialPanelsRef = React.useRef<{
    projectId: string
    left: boolean
    right: boolean
  } | null>(null)

  if (!initialPanelsRef.current || initialPanelsRef.current.projectId !== projectId) {
    initialPanelsRef.current = { projectId, left: showLeft, right: shouldShowCopilot }
  }

  const containerRef = React.useRef<HTMLDivElement>(null)
  const resizingRef = React.useRef<'left' | null>(null)
  const didBootstrapRef = React.useRef<string | null>(null)
  const uploadInputRef = React.useRef<HTMLInputElement>(null)

  React.useEffect(() => {
    if (typeof window === 'undefined') {
      setEntranceStage('done')
      return
    }

    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const initialPanels = initialPanelsRef.current
    if (prefersReducedMotion || !initialPanels || (!initialPanels.left && !initialPanels.right)) {
      setEntranceStage('done')
      return
    }

    setEntranceStage('hold')

    let rafId: number | null = null
    let doneTimer: number | null = null
    const holdTimer = window.setTimeout(() => {
      setEntranceStage('from')
      rafId = window.requestAnimationFrame(() => {
        setEntranceStage('to')
        doneTimer = window.setTimeout(() => {
          setEntranceStage('done')
        }, WORKSPACE_ENTRY_ANIM_MS)
      })
    }, WORKSPACE_ENTRY_HOLD_MS)

    return () => {
      window.clearTimeout(holdTimer)
      if (rafId) window.cancelAnimationFrame(rafId)
      if (doneTimer) window.clearTimeout(doneTimer)
    }
  }, [projectId])

  // Preload file tree after first paint to keep the initial render light.
  React.useEffect(() => {
    if (!projectId) return
    let cancelled = false
    const cleanup = scheduleIdle(() => {
      if (cancelled) return
      loadFiles(projectId).catch((e) => {
        console.error('[WorkspaceLayout] Failed to preload file tree:', e)
      })
    }, 1400)
    return () => {
      cancelled = true
      cleanup()
    }
  }, [projectId, loadFiles])

  const openSearch = React.useCallback(
    (nextQuery?: string, questId?: string) => {
      exitHome()
      openTab({
        pluginId: BUILTIN_PLUGINS.SEARCH,
        context: {
          type: 'custom',
          customData: {
            projectId,
            query: nextQuery ?? undefined,
            questId: questId ?? undefined,
          },
        },
        title: t('plugin_search_title'),
      })
    },
    [exitHome, openTab, projectId, t]
  )

  const openProjectSettings = React.useCallback(() => {
    if (readOnlyMode) return
    exitHome()
    router.push('/settings')
  }, [exitHome, readOnlyMode, router])

  const openSettings = React.useCallback(() => {
    router.push('/settings')
  }, [router])

  const openCommandPalette = React.useCallback(() => {
    setCommandOpen(true)
  }, [])

  const handleGetToken = React.useCallback(async () => {
    setTokenDialogOpen(true)
    setTokenError('')
    setTokenRefreshError('')
    setTokenLoading(true)
    try {
      const data = await getMyToken()
      setMyToken(data.api_token)
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err instanceof Error ? err.message : tCommon('token_load_failed'))
      setTokenError(message)
      setMyToken('')
    } finally {
      setTokenLoading(false)
    }
  }, [tCommon])

  const handleRefreshToken = React.useCallback(async () => {
    if (!myToken) return
    setTokenRefreshError('')
    setTokenRefreshLoading(true)
    try {
      const data = await rotateMyToken(myToken)
      setMyToken(data.api_token)
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err instanceof Error ? err.message : tCommon('token_refresh_failed'))
      setTokenRefreshError(message)
    } finally {
      setTokenRefreshLoading(false)
    }
  }, [myToken, tCommon])


  const handleNewNotebook = React.useCallback(() => {
    exitHome()
    ;(async () => {
      const notebook = await createNotebook(projectId, {
        title: t('command_new_notebook_title'),
        collaborationEnabled: true,
      })
      openTab({
        pluginId: BUILTIN_PLUGINS.NOTEBOOK,
        context: {
          type: 'notebook',
          resourceId: notebook.id,
          resourceName: notebook.title,
          customData: { projectId },
        },
        title: notebook.title,
      })
    })().catch((e) => {
      console.error('[WorkspaceLayout] Failed to create notebook:', e)
      addToast({
        type: 'error',
        title: t('toast_create_notebook_failed'),
        description: tCommon('generic_try_again', undefined, 'Please try again.'),
      })
    })
  }, [addToast, exitHome, openTab, projectId, t, tCommon])

  const handleNewFile = React.useCallback(() => {
    setCreateFileOpen(true)
  }, [])

  const handleNewLatexProject = React.useCallback(() => {
    setCreateLatexOpen(true)
  }, [])

  const handleNewFolder = React.useCallback(() => {
    ;(async () => {
      await createFolder(null, t('command_new_folder_title'))
      addToast({ type: 'success', title: t('toast_folder_created'), duration: 1800 })
    })().catch((e) => {
      console.error('[WorkspaceLayout] Failed to create folder:', e)
      addToast({
        type: 'error',
        title: t('toast_create_folder_failed'),
        description: tCommon('generic_try_again', undefined, 'Please try again.'),
      })
    })
  }, [addToast, createFolder, t, tCommon])

  const handleUploadFiles = React.useCallback(() => {
    uploadInputRef.current?.click()
  }, [])

  const commandItems = React.useMemo<CommandItem[]>(() => {
    return [
      {
        id: 'new-notebook',
        title: t('command_new_notebook_title'),
        description: t('command_new_notebook_desc'),
        group: 'Create',
        keywords: ['create', 'notebook', 'document'],
        icon: (
          <PngIcon
            name="BookOpen"
            size={16}
            className="h-4 w-4"
            fallback={<BookOpen className="h-4 w-4 text-muted-foreground" />}
          />
        ),
        shortcut: 'N',
        run: handleNewNotebook,
      },
      {
        id: 'new-file',
        title: t('command_new_file_title'),
        description: t('command_new_file_desc'),
        group: 'Create',
        keywords: ['create', 'file'],
        icon: <FileText className="h-4 w-4 text-muted-foreground" />,
        run: handleNewFile,
      },
      {
        id: 'new-latex-project',
        title: t('command_new_latex_title'),
        description: t('command_new_latex_desc'),
        group: 'Create',
        keywords: ['latex', 'tex', 'paper', 'overleaf'],
        icon: (
          <PngIcon
            name="Braces"
            size={16}
            className="h-4 w-4"
            fallback={<Braces className="h-4 w-4 text-muted-foreground" />}
          />
        ),
        run: handleNewLatexProject,
      },
      {
        id: 'new-folder',
        title: t('command_new_folder_title'),
        description: t('command_new_folder_desc'),
        group: 'Create',
        keywords: ['create', 'folder', 'directory'],
        icon: <FolderPlus className="h-4 w-4 text-muted-foreground" />,
        run: handleNewFolder,
      },
      {
        id: 'upload-files',
        title: t('command_upload_files_title'),
        description: t('command_upload_files_desc'),
        group: 'Create',
        keywords: ['upload', 'import', 'pdf'],
        icon: <Upload className="h-4 w-4 text-muted-foreground" />,
        run: handleUploadFiles,
      },
      {
        id: 'search',
        title: t('command_open_search_title'),
        description: t('command_open_search_desc'),
        group: 'Tools',
        keywords: ['find', 'search', 'panel'],
        icon: (
          <PngIcon
            name="Search"
            size={16}
            className="h-4 w-4"
            fallback={<Search className="h-4 w-4 text-muted-foreground" />}
          />
        ),
        run: openSearch,
      },
      {
        id: 'settings',
        title: t('command_open_settings_title'),
        description: t('command_open_settings_desc'),
        group: 'Tools',
        keywords: ['preferences', 'config'],
        icon: <Settings className="h-4 w-4 text-muted-foreground" />,
        run: openSettings,
      },
      {
        id: 'toggle-explorer',
        title: showLeft ? t('command_toggle_explorer_hide') : t('command_toggle_explorer_show'),
        description: t('command_toggle_explorer_desc'),
        group: 'Panels',
        keywords: ['left', 'explorer', 'files'],
        icon: <LayoutIcon className="h-4 w-4 text-muted-foreground" />,
        run: () => setShowLeft((v) => !v),
      },
      {
        id: 'toggle-copilot',
        title: copilotDock.state.open ? t('command_toggle_copilot_hide') : t('command_toggle_copilot_show'),
        description: t('command_toggle_copilot_desc'),
        group: 'Panels',
        keywords: ['right', 'assistant', 'ai'],
        icon: <SparklesIcon className="h-4 w-4 text-muted-foreground" />,
        run: () => {
          copilotDock.toggleOpen()
        },
      },
      {
        id: 'back-projects',
        title: t('command_back_projects_title'),
        description: t('command_back_projects_desc'),
        group: 'Navigate',
        keywords: ['projects', 'home'],
        icon: (
          <PngIcon
            name="ArrowLeft"
            size={16}
            className="h-4 w-4"
            fallback={<ArrowLeft className="h-4 w-4 text-muted-foreground" />}
          />
        ),
        run: () => {
          window.location.href = '/projects'
        },
      },
    ]
  }, [
    copilotDock,
    handleNewFile,
    handleNewFolder,
    handleNewLatexProject,
    handleNewNotebook,
    handleUploadFiles,
    openSearch,
    openSettings,
    showLeft,
    t,
  ])

  React.useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const key = e.key.toLowerCase()
      if ((e.metaKey || e.ctrlKey) && key === 'k') {
        e.preventDefault()
        e.stopPropagation()
        openCommandPalette()
      }
    }
    window.addEventListener('keydown', onKeyDown, { capture: true })
    return () => window.removeEventListener('keydown', onKeyDown, { capture: true })
  }, [openCommandPalette])

  React.useEffect(() => {
    if (readOnlyMode) return
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{ text?: unknown; focus?: unknown }>).detail
      const text = typeof detail?.text === 'string' ? detail.text : null
      const focus = Boolean(detail?.focus)
      if (!text) return
      setCopilotPrefill({ text, focus, token: Date.now() })
      if (!homeMode) {
        copilotDock.setOpen(true)
      }
    }
    window.addEventListener('ds:copilot:prefill', handler as EventListener)
    return () => window.removeEventListener('ds:copilot:prefill', handler as EventListener)
  }, [copilotDock, homeMode, readOnlyMode])

  React.useEffect(() => {
    if (readOnlyMode) return
    const handler = (event: Event) => {
      const detail = (
        event as CustomEvent<{ text?: unknown; focus?: unknown; submit?: unknown; newThread?: unknown }>
      ).detail
      const text = typeof detail?.text === 'string' ? detail.text : null
      const focus = Boolean(detail?.focus)
      const submit = Boolean(detail?.submit)
      const newThread = Boolean(detail?.newThread)
      if (!text) return
      if (newThread) {
        copilotActionsRef.current?.startNewThread()
      }
      setCopilotPrefill({ text, focus, token: Date.now() })
      if (!homeMode) {
        copilotDock.setOpen(true)
      }
      if (submit) {
        const attemptSubmit = (triesLeft: number) => {
          const actions = copilotActionsRef.current
          if (actions?.submitComposer) {
            actions.submitComposer()
            return
          }
          if (triesLeft <= 0) return
          window.setTimeout(() => attemptSubmit(triesLeft - 1), 400)
        }
        window.setTimeout(() => attemptSubmit(3), 320)
      }
    }
    window.addEventListener('ds:copilot:run', handler as EventListener)
    return () => window.removeEventListener('ds:copilot:run', handler as EventListener)
  }, [copilotDock, homeMode, readOnlyMode])

  React.useEffect(() => {
    if (readOnlyMode) return
    const handler = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          folderId?: unknown
          buildId?: unknown
          focusedError?: unknown
          promptText?: unknown
        }>
      ).detail
      const folderId = typeof detail?.folderId === 'string' ? detail.folderId : null
      if (!folderId) return
      if (!homeMode) {
        copilotDock.setOpen(true)
      }
      const payload = {
        folderId,
        buildId: typeof detail?.buildId === 'string' ? detail.buildId : null,
        focusedError:
          detail?.focusedError && typeof detail.focusedError === 'object'
            ? (detail.focusedError as {
                kind: 'latex_error'
                tabId?: string
                fileId?: string
                resourceId?: string
                resourcePath?: string
                resourceName?: string
                line?: number
                message: string
                severity: 'error' | 'warning'
                excerpt?: string
              })
            : null,
        promptText: typeof detail?.promptText === 'string' ? detail.promptText : null,
      }
      const attemptRun = (triesLeft: number) => {
        const actions = copilotActionsRef.current
        if (actions?.runFixWithAi) {
          actions.runFixWithAi(payload)
          return
        }
        if (triesLeft <= 0) return
        window.setTimeout(() => attemptRun(triesLeft - 1), 320)
      }
      window.setTimeout(() => attemptRun(4), 220)
    }
    window.addEventListener('ds:copilot:fix-with-ai', handler as EventListener)
    return () => window.removeEventListener('ds:copilot:fix-with-ai', handler as EventListener)
  }, [copilotDock, homeMode, readOnlyMode])

  React.useEffect(() => {
    if (readOnlyMode) return
    const focusComposer = (triesLeft: number) => {
      const actions = copilotActionsRef.current
      if (actions?.focusComposer) {
        actions.focusComposer()
        return
      }
      if (triesLeft <= 0) return
      window.setTimeout(() => focusComposer(triesLeft - 1), 260)
    }
    const handler = (event?: Event) => {
      const detail = (event as CustomEvent<{ focus?: unknown }> | undefined)?.detail
      const shouldFocus = Boolean(detail?.focus)
      if (homeMode) {
        pendingCopilotOpenRef.current = true
        return
      }
      copilotDock.setOpen(true)
      if (shouldFocus) {
        window.setTimeout(() => focusComposer(4), 160)
      }
    }
    window.addEventListener('ds:copilot:open', handler as EventListener)
    window.addEventListener('ds:copilot:focus', handler as EventListener)
    return () => {
      window.removeEventListener('ds:copilot:open', handler as EventListener)
      window.removeEventListener('ds:copilot:focus', handler as EventListener)
    }
  }, [copilotDock, homeMode, readOnlyMode])

  React.useEffect(() => {
    if (readOnlyMode) return
    if (homeMode) return
    if (!pendingCopilotOpenRef.current) return
    pendingCopilotOpenRef.current = false
    copilotDock.setOpen(true)
  }, [copilotDock, homeMode, readOnlyMode])

  React.useEffect(() => {
    if (readOnlyMode || !tabsHydrated) return
    // Ensure we bootstrap once per project.
    if (didBootstrapRef.current === projectId) return
    didBootstrapRef.current = projectId

    let canceled = false

    const ensureDefaultNotebook = async () => {
      // 1) If persisted tabs belong to a different project (or have no projectId),
      //    reset tabs to prevent cross-project leakage.
      const state = useTabsStore.getState()
      let tabs = state.tabs
      let hasProjectTabs = tabs.some((t) => tabMatchesProject(t, projectId))
      const hasForeignTabs = tabs.some((t) => tabIsForeignProject(t, projectId))
      if (hasForeignTabs) {
        resetTabs()
        const nextState = useTabsStore.getState()
        tabs = nextState.tabs
        hasProjectTabs = tabs.some((t) => tabMatchesProject(t, projectId))
      }
      const tabsWithoutAutoFigure = tabs.filter(
        (tab) => !(tab.pluginId === BUILTIN_PLUGINS.AUTOFIGURE && tabMatchesProject(tab, projectId))
      )
      if (tabsWithoutAutoFigure.length !== tabs.length) {
        const currentActiveTabId = useTabsStore.getState().activeTabId
        const nextActiveTabId = tabsWithoutAutoFigure.some((tab) => tab.id === currentActiveTabId)
          ? currentActiveTabId
          : tabsWithoutAutoFigure
              .filter((tab) => tabMatchesProject(tab, projectId))
              .sort((a, b) => (b.lastAccessedAt || 0) - (a.lastAccessedAt || 0))[0]?.id ??
            tabsWithoutAutoFigure[tabsWithoutAutoFigure.length - 1]?.id ??
            null
        useTabsStore.setState({
          tabs: tabsWithoutAutoFigure,
          activeTabId: nextActiveTabId,
        })
        tabs = tabsWithoutAutoFigure
        hasProjectTabs = tabs.some((t) => tabMatchesProject(t, projectId))
      }
      if (isLocalQuestProject) {
        const visibleQuestTabs = tabs.filter(
          (t) => tabMatchesProject(t, projectId) && isQuestFriendlyTab(t, projectId)
        )
        if (visibleQuestTabs.length === 0) {
          openTab({
            pluginId: QUEST_WORKSPACE_PLUGIN_ID,
            context: buildQuestWorkspaceTabContext(projectId, 'canvas'),
            title: 'Canvas',
          })
          return
        }
        const activeTabId = useTabsStore.getState().activeTabId
        const activeTab = visibleQuestTabs.find((t) => t.id === activeTabId)
        if (activeTab) {
          return
        }
        const mostRecentQuestTab = visibleQuestTabs.sort(
          (a, b) => (b.lastAccessedAt || 0) - (a.lastAccessedAt || 0)
        )[0]
        if (mostRecentQuestTab) {
          useTabsStore.getState().setActiveTab(mostRecentQuestTab.id)
        }
        return
      }
      if (!hasProjectTabs) {
        openTab({
          pluginId: BUILTIN_PLUGINS.LAB,
          context: {
            type: 'custom',
            customData: {
              projectId,
              readOnly: readOnlyMode,
            },
          },
          title: t('plugin_lab_home_title'),
        })
        return
      }
      const activeTabId = useTabsStore.getState().activeTabId
      const activeTab = tabs.find((t) => t.id === activeTabId)
      if (activeTab && tabMatchesProject(activeTab, projectId)) {
        return
      }

      const mostRecentProjectTab = tabs
        .filter((t) => tabMatchesProject(t, projectId))
        .sort((a, b) => (b.lastAccessedAt || 0) - (a.lastAccessedAt || 0))[0]
      if (mostRecentProjectTab) {
        useTabsStore.getState().setActiveTab(mostRecentProjectTab.id)
        return
      }

      const stateAfterReset = useTabsStore.getState()
      const existingLabTab = stateAfterReset.tabs.find((t) => {
        return t.pluginId === BUILTIN_PLUGINS.LAB && tabMatchesProject(t, projectId)
      })
      if (existingLabTab) {
        stateAfterReset.setActiveTab(existingLabTab.id)
        return
      }

      // 2) If a notebook tab for this project already exists, activate it.
      const existingNotebookTab = stateAfterReset.tabs.find((t) => {
        return (
          t.pluginId === BUILTIN_PLUGINS.NOTEBOOK &&
          tabMatchesProject(t, projectId) &&
          t.context?.type === 'notebook' &&
          typeof t.context?.resourceId === 'string'
        )
      })
      if (existingNotebookTab) {
        stateAfterReset.setActiveTab(existingNotebookTab.id)
        return
      }

      // 3) Resolve/create a default notebook, then open it.
      const storageKey = `ds:project:${projectId}:homeNotebookId`
      const preferredId =
        typeof window !== 'undefined' ? window.localStorage.getItem(storageKey) : null

      if (preferredId) {
        try {
          const notebook = await getNotebook(preferredId)
          openTab({
            pluginId: BUILTIN_PLUGINS.NOTEBOOK,
            context: {
              type: 'notebook',
              resourceId: notebook.id,
              resourceName: notebook.title,
              customData: { projectId },
            },
            title: notebook.title,
          })
          return
        } catch (e) {
          // Stale id (deleted/moved/no permission) -> clear and fallback.
          if (typeof window !== 'undefined') {
            window.localStorage.removeItem(storageKey)
          }
        }
      }

      const list = await listNotebooks(projectId, { skip: 0, limit: 1 })
      const notebook =
        list.items[0] ??
        (await createNotebook(projectId, {
          title: 'Agent',
          collaborationEnabled: true,
        }))

      if (canceled) return

      if (typeof window !== 'undefined') {
        window.localStorage.setItem(storageKey, notebook.id)
      }

      openTab({
        pluginId: BUILTIN_PLUGINS.NOTEBOOK,
        context: {
          type: 'notebook',
          resourceId: notebook.id,
          resourceName: notebook.title,
          customData: { projectId },
        },
        title: notebook.title,
      })
    }

    ensureDefaultNotebook().catch((e) => {
      console.error('[WorkspaceLayout] Failed to bootstrap default notebook:', e)
    })

    return () => {
      canceled = true
    }
  }, [isLocalQuestProject, openTab, projectId, readOnlyMode, resetTabs, t, tabsHydrated])

  const startResize = (direction: 'left') => (e: React.MouseEvent) => {
    e.preventDefault()
    resizingRef.current = direction
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  React.useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!resizingRef.current || !containerRef.current) return
      const containerRect = containerRef.current.getBoundingClientRect()

      if (resizingRef.current === 'left') {
        const newWidth = e.clientX - containerRect.left
        if (newWidth > 200 && newWidth < 500) setLeftWidth(newWidth)
      }
    }

    const handleMouseUp = () => {
      resizingRef.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  // Track Stage width for Copilot constraints & safe-area calculations.
  React.useEffect(() => {
    const el = stageEl
    if (!el) return
    const clampToStage = copilotDock.clampToStage
    const maxRatio = copilotDock.state.maxRatio
    let rafId: number | null = null

    const update = () => {
      const width = Math.round(el.getBoundingClientRect().width)
      setStageWidth((prev) => (prev === width ? prev : width))
      clampToStage(width, { maxRatio })
    }

    const scheduleUpdate = () => {
      if (rafId !== null) return
      rafId = window.requestAnimationFrame(() => {
        rafId = null
        update()
      })
    }

    update()
    const ro = new ResizeObserver(scheduleUpdate)
    ro.observe(el)
    return () => {
      if (rafId !== null) {
        window.cancelAnimationFrame(rafId)
      }
      ro.disconnect()
    }
  }, [copilotDock.clampToStage, copilotDock.state.maxRatio, stageEl])

  const entranceData =
    entranceStage === 'from' || entranceStage === 'to' ? entranceStage : undefined
  const showLeftPanel = showLeft && entranceStage !== 'hold'
  const showCopilotPanel = shouldShowCopilot && entranceStage !== 'hold'
  const applyCopilotPadding =
    shouldShowCopilot && (entranceStage === 'to' || entranceStage === 'done')
  const desiredScrollbarSide: ScrollbarSide = showCopilotPanel
    ? copilotDock.state.side === 'right'
      ? 'left'
      : 'right'
    : 'right'

  React.useEffect(() => {
    if (typeof window === 'undefined') return

    const timers = scrollbarTimersRef.current
    if (timers?.out) window.clearTimeout(timers.out)
    if (timers?.done) window.clearTimeout(timers.done)
    scrollbarTimersRef.current = null

    if (!showCopilotPanel) {
      setScrollbarSide('right')
      setScrollbarFadePhase('idle')
      return
    }

    if (desiredScrollbarSide === scrollbarSide) return

    const totalMs = getCopilotSwitchDurationMs(stageWidth, copilotDock.state.width)
    const phaseMs = Math.max(140, Math.round(totalMs / 2))
    setScrollbarFadeMs(phaseMs)
    setScrollbarFadePhase('out')

    const outTimer = window.setTimeout(() => {
      setScrollbarSide(desiredScrollbarSide)
      setScrollbarFadePhase('in')
    }, phaseMs)

    const doneTimer = window.setTimeout(() => {
      setScrollbarFadePhase('idle')
    }, totalMs)

    scrollbarTimersRef.current = { out: outTimer, done: doneTimer }

    return () => {
      if (outTimer) window.clearTimeout(outTimer)
      if (doneTimer) window.clearTimeout(doneTimer)
      scrollbarTimersRef.current = null
    }
  }, [
    copilotDock.state.width,
    desiredScrollbarSide,
    scrollbarSide,
    showCopilotPanel,
    stageWidth,
  ])

  return (
    <div
      id="workspace-root"
      data-tooltip-layer="active"
      data-copilot-open={showCopilotPanel ? 'true' : 'false'}
      data-copilot-side={copilotDock.state.side}
      data-scrollbar-side={scrollbarSide}
      data-scrollbar-fade={scrollbarFadePhase}
      className={cn(
        'relative isolate min-h-screen overflow-hidden bg-[#ABA9A5] dark:bg-[#0B0C0E]',
        navbarCollapsed && 'navbar-collapsed',
        !showLeftPanel && 'navbar-left-hidden',
        navbarMotion === 'collapse' && 'navbar-motion-collapse',
        navbarMotion === 'expand' && 'navbar-motion-expand'
      )}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        padding: 8,
        gap: navbarCollapsed ? 0 : 8,
        overflow: 'hidden',
        '--workspace-left-width': `${leftWidth}px`,
        '--ws-scrollbar-fade-ms': `${scrollbarFadeMs}ms`,
      } as React.CSSProperties}
    >
      {/* Atmosphere background (shared with /projects) */}
      <div aria-hidden className="pointer-events-none absolute inset-0 -z-10">
        <div
          className={cn(
            'absolute -top-40 -left-40 h-[560px] w-[560px] rounded-full blur-3xl animate-blob',
            'bg-[radial-gradient(circle_at_center,rgba(143,163,184,0.16),transparent_72%)]',
            'dark:bg-[radial-gradient(circle_at_center,rgba(143,163,184,0.16),transparent_72%)]'
          )}
        />
        <div
          className={cn(
            'absolute top-10 -right-52 h-[640px] w-[640px] rounded-full blur-3xl animate-blob',
            'bg-[radial-gradient(circle_at_center,rgba(47,52,55,0.08),transparent_72%)]',
            'dark:bg-[radial-gradient(circle_at_center,rgba(47,52,55,0.10),transparent_72%)]'
          )}
          style={{ animationDelay: '1.5s' }}
        />
        <Noise size={260} className="opacity-[0.04] dark:opacity-[0.05]" />
      </div>

      <div className={cn('workspace-navbar-shell', navbarCollapsed && 'is-collapsed')}>
        <Navbar
          projectId={projectId}
          projectName={projectName}
          onToggleLeft={() => setShowLeft(!showLeft)}
          onToggleRight={() => {
            copilotDock.toggleOpen()
          }}
          onOpenCommandPalette={openCommandPalette}
          onOpenSettings={openSettings}
          onOpenProjectSettings={openProjectSettings}
          onShare={() => setShareDialogOpen(true)}
          showShare={canShare || canCopy || shareCopyAllowed}
          onNewNotebook={handleNewNotebook}
          onNewFile={handleNewFile}
          onNewLatexProject={handleNewLatexProject}
          onNewFolder={handleNewFolder}
          onUploadFiles={handleUploadFiles}
          leftVisible={showLeft}
          rightVisible={homeMode || copilotDock.state.open}
          rightLocked={homeMode}
          readOnly={readOnlyMode}
          collapsed={navbarCollapsed}
          onToggleCollapse={toggleNavbarCollapsed}
          onExitHome={exitHome}
          onTabSelect={handleTabSelect}
          localQuestMode={isLocalQuestProject}
        />
      </div>

      {(canShare || canCopy || shareCopyAllowed) && (
        <ProjectShareDialog
          projectId={projectId}
          open={shareDialogOpen}
          onOpenChange={setShareDialogOpen}
          canManageShare={canShare && !readOnlyMode}
          defaultTab={canShare && !readOnlyMode ? 'share' : 'copy'}
        />
      )}

      {!readOnlyMode && (
        <>
          <input
            ref={uploadInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files || [])
              if (files.length === 0) return
              ;(async () => {
                await upload(null, files)
                addToast({
                  type: 'success',
                  title: t('toast_upload_started'),
                  description: t('toast_upload_started_desc', { count: files.length }),
                  duration: 2200,
                })
              })().catch((err) => {
                console.error('[WorkspaceLayout] Upload failed:', err)
                addToast({
                  type: 'error',
                  title: t('toast_upload_failed'),
                  description: tCommon('generic_try_again', undefined, 'Please try again.'),
                })
              })
              e.target.value = ''
            }}
          />

          <CreateFileDialog
            open={createFileOpen}
            onOpenChange={setCreateFileOpen}
            parentId={null}
            onCreated={(file) => {
              exitHome()
              if (file.type === 'folder' && file.folderKind === 'latex') {
                openTab({
                  pluginId: '@ds/plugin-latex',
                  context: {
                    type: 'custom',
                    resourceId: file.id,
                    resourceName: file.name,
                    customData: {
                      projectId,
                      latexFolderId: file.id,
                      mainFileId: file.latex?.mainFileId ?? null,
                      readOnly: readOnlyMode,
                    },
                  },
                  title: file.name,
                })
                return
              }
              void openFileInTab(file, {
                customData: {
                  projectId,
                  fileMeta: {
                    updatedAt: file.updatedAt,
                    sizeBytes: file.size,
                    mimeType: file.mimeType,
                  },
                },
              })
            }}
          />

          <CreateLatexProjectDialog
            open={createLatexOpen}
            onOpenChange={setCreateLatexOpen}
            parentId={null}
            onCreated={(folder) => {
              exitHome()
              openTab({
                pluginId: '@ds/plugin-latex',
                context: {
                  type: 'custom',
                  resourceId: folder.id,
                  resourceName: folder.name,
                  customData: {
                    projectId,
                    latexFolderId: folder.id,
                    mainFileId: folder.latex?.mainFileId ?? null,
                    readOnly: readOnlyMode,
                  },
                },
                title: folder.name,
              })
            }}
          />
        </>
      )}

      <div
        className={cn('workspace-container', showLeftPanel ? 'has-left' : 'no-left')}
        data-entrance={entranceData}
        ref={containerRef}
      >
        {/* Left Panel */}
        {showLeftPanel && (
          <>
            <LeftPanel
              width={leftWidth}
              projectId={projectId}
              onClose={() => setShowLeft(false)}
              readOnly={readOnlyMode}
              onEnterHome={enterHome}
              onEnterLab={enterLab}
              onExitHome={exitHome}
              localQuestMode={isLocalQuestProject}
            />
            <div className="resizer" onMouseDown={startResize('left')} />
          </>
        )}

        {/* Stage (Center + Agent) */}
        <div className="workspace-stage-shell" ref={stageRef}>
          <div
            className={cn(
              'workspace-stage-layer workspace-center-layer',
              homeMode && 'is-hidden'
            )}
          >
            <CenterPanel
              projectId={projectId}
              readOnly={readOnlyMode}
              localQuestMode={isLocalQuestProject}
              safePaddingLeft={
                applyCopilotPadding && copilotDock.state.side === 'left'
                  ? copilotDock.state.width + COPILOT_DOCK_DEFAULTS.gap + COPILOT_DOCK_DEFAULTS.edgeInset
                  : 0
              }
              safePaddingRight={
                applyCopilotPadding && copilotDock.state.side === 'right'
                  ? copilotDock.state.width + COPILOT_DOCK_DEFAULTS.gap + COPILOT_DOCK_DEFAULTS.edgeInset
                  : 0
              }
              overlay={
                !readOnlyMode && (!homeMode || homeEntering) ? (
                  <CopilotDockOverlay
                    projectId={projectId}
                    stageWidth={stageWidth}
                    state={copilotDock.state}
                    surfaceMode="copilot"
                    prefill={copilotPrefill}
                    visible={showCopilotPanel}
                    headerContent={
                      isLabTab && !isLocalQuestProject ? (
                        <LabCopilotHeader
                          disabled={shareReadOnly}
                          agents={agents}
                          templates={templates}
                          quests={quests}
                          onClearChat={handleLabClearChat}
                          clearChatDisabled={labCopilotReadOnly}
                        />
                      ) : undefined
                    }
                    bodyContent={
                      isLocalQuestProject ? (
                        <QuestCopilotDockPanel
                          questId={projectId}
                          title={workspaceProjectTitle}
                          readOnly={readOnlyMode}
                          prefill={copilotPrefill}
                        />
                      ) : isLabTab ? (
                          <LabCopilotPanel
                            projectId={projectId}
                            readOnly={readOnlyMode}
                            shareReadOnly={shareReadOnly}
                            cliStatus={effectiveCliStatus}
                            templates={templates}
                            agents={agents}
                            quests={quests}
                            prefill={copilotPrefill}
                            onActionsChange={(actions) => {
                              copilotActionsRef.current = actions
                            }}
                          />
                        
                      ) : undefined
                    }
                    hideNewChat={isLabTab || isLocalQuestProject}
                    hideHistory={isLabTab || isLocalQuestProject}
                    hideFixWithAi={isLocalQuestProject}
                    onActionsChange={(actions) => {
                      copilotActionsRef.current = actions
                    }}
                    onClose={() => {
                      copilotDock.setOpen(false)
                    }}
                    setSide={copilotDock.setSide}
                    toggleSide={copilotDock.toggleSide}
                    setWidth={copilotDock.setWidth}
                    setMaxRatio={copilotDock.setMaxRatio}
                    readOnly={readOnlyMode}
                  />
                ) : null
              }
              onExitHome={exitHome}
            />
          </div>
          <div
            className={cn(
              'workspace-stage-layer workspace-home-layer',
              !homeMode && 'is-hidden'
            )}
          >
            {homeVisible ? (
              <WelcomeStage
                projectId={projectId}
                readOnly={readOnlyMode}
                visible={homeMode}
                prefill={copilotPrefill}
                onActionsChange={(actions) => {
                  copilotActionsRef.current = actions
                }}
                onExitHome={exitHome}
              />
            ) : null}
          </div>
        </div>

        {/* Floating Recovery Toggles */}
        {entranceStage === 'done' && !showLeft && (
          <div
            className="panel-toggle toggle-left"
            onClick={() => setShowLeft(true)}
            title={t('workspace_open_explorer')}
          >
            <LayoutIcon />
          </div>
        )}
        {entranceStage === 'done' && !readOnlyMode && !homeMode && !copilotDock.state.open && (
          <div
            className="panel-toggle toggle-right"
            onClick={() => copilotDock.setOpen(true)}
            title={t('workspace_open_copilot')}
          >
            <SparklesIcon />
          </div>
        )}
      </div>

      <WorkspaceCommandPalette
        open={commandOpen}
        onOpenChange={setCommandOpen}
        items={commandItems}
        projectId={projectId}
        onExitHome={exitHome}
        onEnterLab={enterLab}
        onOpenShareDialog={() => setShareDialogOpen(true)}
        tokenActions={{
          onGetToken: handleGetToken,
          onRefreshToken: handleRefreshToken,
          hasToken: Boolean(myToken),
        }}
      />
      <TokenDialog
        open={tokenDialogOpen}
        onOpenChange={setTokenDialogOpen}
        title={tCommon('token_dialog_title')}
        description={tCommon('token_dialog_description')}
        token={myToken}
        loading={tokenLoading}
        error={tokenError}
        onRefresh={handleRefreshToken}
        refreshLoading={tokenRefreshLoading}
        refreshDisabled={!myToken || tokenLoading}
        refreshError={tokenRefreshError}
      />
      <WorkspaceTooltipLayer rootId="workspace-root" />
    </div>
  )
}

export default WorkspaceLayout
