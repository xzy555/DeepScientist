'use client'

import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Clock3,
  FileCode2,
  FlaskConical,
  GitBranch,
  Lightbulb,
  Plus,
  RefreshCw,
  Sparkles,
  Square,
  Terminal,
} from 'lucide-react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { Button } from '@/components/ui/button'
import { useToast } from '@/components/ui/toast'
import { client } from '@/lib/api'
import { getBashLogs, stopBashSession } from '@/lib/api/bash'
import { ensureTerminalSession, sendTerminalInput } from '@/lib/api/terminal'
import { useQuestWorkspace } from '@/lib/acp'
import { openQuestDocumentAsFileNode } from '@/lib/api/quest-files'
import { useOpenFile } from '@/hooks/useOpenFile'
import { useBashLogStream } from '@/lib/hooks/useBashLogStream'
import { useBashSessionStream } from '@/lib/hooks/useBashSessionStream'
import { EnhancedTerminal } from '@/lib/plugins/cli/components/EnhancedTerminal'
import LabSurface from '@/lib/plugins/lab/components/LabSurface'
import { useLabCopilotStore } from '@/lib/stores/lab-copilot'
import { useLabGraphSelectionStore } from '@/lib/stores/lab-graph-selection'
import { cn } from '@/lib/utils'
import { getProgressPercent } from '@/lib/utils/bash-progress'
import type { BashLogEntry, BashProgress, BashSession } from '@/lib/types/bash'
import {
  isBashProgressMarker,
  parseBashStatusMarker,
  splitBashLogLine,
} from '@/lib/utils/bash-log'
import type {
  FeedItem,
  GitBranchesPayload,
  GuidanceVm,
  MetricsTimelinePayload,
  MetricTimelineSeries,
} from '@/types'
import {
  QUEST_WORKSPACE_VIEW_EVENT,
  type QuestWorkspaceView,
  type QuestWorkspaceViewDetail,
} from '@/components/workspace/workspace-events'
import '@/lib/plugins/cli/styles/terminal.css'

type LinkItem = {
  key: string
  title: string
  subtitle?: string | null
  badge?: string | null
  documentId?: string | null
}

export type QuestWorkspaceState = ReturnType<typeof useQuestWorkspace>

type QuestWorkspaceSurfaceInnerProps = {
  questId: string
  safePaddingLeft: number
  safePaddingRight: number
  overlay?: React.ReactNode
  view?: QuestWorkspaceView
  onViewChange?: (view: QuestWorkspaceView) => void
  workspace: QuestWorkspaceState
}

function flattenText(value?: string | null) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
}

function clampText(value?: string | null, limit = 180) {
  const normalized = flattenText(value)
  if (!normalized) return '—'
  if (normalized.length <= limit) return normalized
  return `${normalized.slice(0, Math.max(0, limit - 1)).trimEnd()}…`
}

function formatRelativeTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function formatDuration(value?: string | null) {
  if (!value) return '—'
  const start = new Date(value)
  if (Number.isNaN(start.getTime())) return '—'
  const diffMs = Math.max(0, Date.now() - start.getTime())
  const totalMinutes = Math.floor(diffMs / 60000)
  const days = Math.floor(totalMinutes / (60 * 24))
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60)
  const minutes = totalMinutes % 60
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

function formatBashSessionStatus(status?: string | null) {
  if (!status) return 'idle'
  return status.replace(/_/g, ' ')
}

function sortBashLogEntries(entries: BashLogEntry[]) {
  return [...entries].sort((a, b) => a.seq - b.seq)
}

function mergeBashLogEntries(current: BashLogEntry[], incoming: BashLogEntry[]) {
  const map = new Map<number, BashLogEntry>()
  current.forEach((entry) => {
    map.set(entry.seq, entry)
  })
  incoming.forEach((entry) => {
    map.set(entry.seq, entry)
  })
  return sortBashLogEntries(Array.from(map.values()))
}

function formatConnectionState(
  value?: ReturnType<typeof useQuestWorkspace>['connectionState']
) {
  if (!value || value === 'connected') return 'live'
  return value.replace(/_/g, ' ')
}

function formatMetricValue(
  value?: number | string | null,
  decimals?: number | null
) {
  if (value == null || value === '') return '—'
  if (typeof value === 'number') {
    if (typeof decimals === 'number') {
      return value.toFixed(decimals)
    }
    return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
  }
  return String(value)
}

function MetricTimelineCard({
  series,
  primaryMetricId,
}: {
  series: MetricTimelineSeries
  primaryMetricId?: string | null
}) {
  const chartData = React.useMemo(
    () =>
      (series.points || []).map((point) => ({
        seq: point.seq,
        value: point.value,
        runId: point.run_id,
        delta: point.delta_vs_baseline,
        breakthrough: point.breakthrough,
      })),
    [series.points]
  )
  const yValues = [
    ...chartData.map((item) => item.value).filter((item): item is number => typeof item === 'number'),
    ...(series.baselines || [])
      .map((item) => item.value)
      .filter((item): item is number => typeof item === 'number'),
  ]
  const minValue = yValues.length ? Math.min(...yValues) : undefined
  const maxValue = yValues.length ? Math.max(...yValues) : undefined
  const yDomain =
    typeof minValue === 'number' && typeof maxValue === 'number'
      ? [minValue === maxValue ? minValue - 1 : minValue, minValue === maxValue ? maxValue + 1 : maxValue]
      : ['auto', 'auto']

  return (
    <div className="overflow-hidden rounded-[26px] border border-black/[0.08] bg-[linear-gradient(180deg,rgba(255,255,255,0.86),rgba(244,239,233,0.94))] p-4 shadow-card dark:border-white/[0.10] dark:bg-[linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.03))]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-foreground">{series.label || series.metric_id}</div>
          <div className="mt-1 text-xs text-muted-foreground">
            {series.metric_id}
            {series.direction ? ` · ${series.direction}` : ''}
            {series.unit ? ` · ${series.unit}` : ''}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {primaryMetricId === series.metric_id ? <StatusPill>primary</StatusPill> : null}
          {(series.baselines || []).map((baseline) => (
            <StatusPill key={`${baseline.label}:${baseline.metric_id}`}>
              {baseline.selected ? 'selected baseline' : baseline.label}
            </StatusPill>
          ))}
        </div>
      </div>

      <div className="mt-4 h-[220px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="4 6" stroke="rgba(120,120,120,0.18)" />
            <XAxis
              dataKey="seq"
              tickLine={false}
              axisLine={false}
              tick={{ fill: 'currentColor', fontSize: 11 }}
            />
            <YAxis
              tickLine={false}
              axisLine={false}
              tick={{ fill: 'currentColor', fontSize: 11 }}
              domain={yDomain as [number, number] | ['auto', 'auto']}
            />
            <Tooltip
              contentStyle={{
                borderRadius: 18,
                border: '1px solid rgba(0,0,0,0.08)',
                background: 'rgba(255,255,255,0.94)',
                boxShadow: '0 18px 42px -34px rgba(17,24,39,0.18)',
              }}
              formatter={(value: number | string | null | undefined) => formatMetricValue(value, series.decimals)}
              labelFormatter={(label) => `Run #${label}`}
            />
            {(series.baselines || []).map((baseline) =>
              typeof baseline.value === 'number' ? (
                <ReferenceLine
                  key={`${baseline.label}:${baseline.value}`}
                  y={baseline.value}
                  stroke={baseline.selected ? 'rgba(148,118,66,0.8)' : 'rgba(143,163,184,0.64)'}
                  strokeDasharray="6 6"
                  ifOverflow="extendDomain"
                />
              ) : null
            )}
            <Line
              type="monotone"
              dataKey="value"
              stroke="rgba(91,112,131,0.96)"
              strokeWidth={2.4}
              dot={{ r: 3, fill: 'rgba(91,112,131,0.96)' }}
              activeDot={{ r: 5 }}
              connectNulls
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
        <span>{chartData.length} runs</span>
        {chartData.length ? (
          <span>
            latest {formatMetricValue(chartData[chartData.length - 1]?.value, series.decimals)}
          </span>
        ) : (
          <span>No points yet</span>
        )}
      </div>
    </div>
  )
}

function summarizeFeedItem(item: FeedItem) {
  if (item.type === 'message') {
    return {
      category: item.reasoning ? 'Thinking' : item.role === 'assistant' ? 'Assistant' : 'User',
      title: item.skillId ? `${item.role} · ${item.skillId}` : item.role,
      summary: clampText(item.content, 220),
      createdAt: item.createdAt,
    }
  }

  if (item.type === 'artifact') {
    return {
      category: 'Artifact',
      title: item.kind,
      summary: clampText(item.reason || item.guidance || item.content, 220),
      createdAt: item.createdAt,
    }
  }

  if (item.type === 'operation') {
    const toolLabel =
      item.toolName ||
      [item.mcpServer, item.mcpTool].filter(Boolean).join('.') ||
      item.label
    return {
      category: item.label === 'tool_result' ? 'Tool result' : 'Tool call',
      title: toolLabel,
      summary: clampText(item.subject || item.output || item.args || item.content, 220),
      createdAt: item.createdAt,
    }
  }

  return {
    category: 'Event',
    title: item.label,
    summary: clampText(item.content, 220),
    createdAt: item.createdAt,
  }
}

function StatusPill({
  children,
  mono = false,
}: {
  children: React.ReactNode
  mono?: boolean
}) {
  return (
    <span
      className={cn(
        'inline-flex max-w-full items-center rounded-full border border-black/[0.08] px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground dark:border-white/[0.12]',
        mono && 'font-mono normal-case tracking-[0.02em]'
      )}
    >
      <span className="truncate">{children}</span>
    </span>
  )
}

function WorkspaceRefreshButton({
  onRefresh,
  label = 'Refresh',
}: {
  onRefresh: () => Promise<void> | void
  label?: string
}) {
  const [refreshing, setRefreshing] = React.useState(false)

  const handleClick = React.useCallback(async () => {
    setRefreshing(true)
    try {
      await onRefresh()
    } finally {
      setRefreshing(false)
    }
  }, [onRefresh])

  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      onClick={() => {
        void handleClick()
      }}
      className="h-9 rounded-full border-black/[0.08] bg-white/[0.84] px-3 text-[11px] shadow-sm backdrop-blur hover:bg-white dark:border-white/[0.10] dark:bg-[rgba(18,18,18,0.72)] dark:hover:bg-[rgba(24,24,24,0.9)]"
    >
      <RefreshCw className={cn('mr-1.5 h-3.5 w-3.5', refreshing && 'animate-spin')} />
      {label}
    </Button>
  )
}

function DetailSection({
  title,
  hint,
  actions,
  children,
  first = false,
}: {
  title: string
  hint?: string | null
  actions?: React.ReactNode
  children: React.ReactNode
  first?: boolean
}) {
  return (
    <section
      className={cn(
        'py-6',
        first ? 'pt-0' : 'border-t border-dashed border-black/[0.12] dark:border-white/[0.12]'
      )}
    >
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            {title}
          </div>
          {hint ? (
            <div className="mt-2 max-w-3xl text-sm leading-7 text-muted-foreground">
              {hint}
            </div>
          ) : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </div>
      {children}
    </section>
  )
}

function OverviewMetric({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode
  label: string
  value: React.ReactNode
  hint?: string | null
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
        <span className="shrink-0">{icon}</span>
        <span>{label}</span>
      </div>
      <div className="mt-2 break-words text-[15px] font-semibold leading-6 text-foreground">
        {value}
      </div>
      {hint ? (
        <div className="mt-2 break-words text-sm leading-6 text-muted-foreground">
          {hint}
        </div>
      ) : null}
    </div>
  )
}

function DocumentListBlock({
  title,
  countLabel,
  items,
  emptyLabel,
  onOpen,
}: {
  title: string
  countLabel?: string | null
  items: LinkItem[]
  emptyLabel: string
  onOpen: (item: LinkItem) => void
}) {
  return (
    <div className="min-w-0">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
          {title}
        </div>
        {countLabel ? <div className="text-[11px] text-muted-foreground">{countLabel}</div> : null}
      </div>
      {items.length === 0 ? (
        <div className="py-3 text-sm leading-7 text-muted-foreground">{emptyLabel}</div>
      ) : (
        <div className="divide-y divide-dashed divide-black/[0.10] dark:divide-white/[0.10]">
          {items.map((item) => {
            const body = (
              <>
                <div className="min-w-0">
                  <div className="break-words text-sm font-medium leading-6 text-foreground">
                    {item.title}
                  </div>
                  {item.subtitle ? (
                    <div className="mt-1 break-words text-sm leading-6 text-muted-foreground">
                      {item.subtitle}
                    </div>
                  ) : null}
                </div>
                {item.badge ? (
                  <div className="shrink-0 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                    {item.badge}
                  </div>
                ) : null}
              </>
            )

            if (!item.documentId) {
              return (
                <div key={item.key} className="flex items-start justify-between gap-3 py-3">
                  {body}
                </div>
              )
            }

            return (
              <button
                key={item.key}
                type="button"
                onClick={() => onOpen(item)}
                className="flex w-full items-start justify-between gap-3 py-3 text-left transition hover:text-foreground"
              >
                {body}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ActivityTimeline({
  items,
  loading,
  restoring,
  connectionState,
}: {
  items: FeedItem[]
  loading: boolean
  restoring: boolean
  connectionState: ReturnType<typeof useQuestWorkspace>['connectionState']
}) {
  if (items.length === 0) {
    const label =
      restoring || loading
        ? 'Loading recent quest activity…'
        : connectionState === 'reconnecting'
          ? 'Reconnecting to quest event stream…'
          : connectionState === 'error'
            ? 'Quest event stream is temporarily unavailable.'
            : 'No quest activity yet.'

    return <div className="py-3 text-sm leading-7 text-muted-foreground">{label}</div>
  }

  return (
    <div className="divide-y divide-dashed divide-black/[0.10] dark:divide-white/[0.10]">
      {items.map((item) => {
        const summary = summarizeFeedItem(item)
        return (
          <div
            key={item.id}
            className="grid gap-3 py-3 sm:grid-cols-[112px_minmax(0,1fr)]"
          >
            <div className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
              {formatRelativeTime(summary.createdAt)}
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-foreground">
                  {summary.category}
                </span>
                <span className="text-[11px] text-muted-foreground">{summary.title}</span>
              </div>
              <div className="mt-2 break-words text-sm leading-7 text-muted-foreground">
                {summary.summary}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function QuestCanvasSurface({
  questId,
  feed,
  error,
  onRefresh,
}: {
  questId: string
  feed: FeedItem[]
  error?: string | null
  onRefresh: () => Promise<void>
}) {
  const queryClient = useQueryClient()
  const clearGraphSelection = useLabGraphSelectionStore((state) => state.clear)
  const liveCanvasKey = React.useMemo(
    () =>
      feed
        .filter((item) => !(item.type === 'message' && item.role === 'user'))
        .slice(-20)
        .map((item) => item.id)
        .join('|'),
    [feed]
  )

  React.useEffect(() => {
    clearGraphSelection()
    return () => {
      clearGraphSelection()
    }
  }, [clearGraphSelection, questId])

  const handleRefresh = React.useCallback(async () => {
    clearGraphSelection()
    await Promise.allSettled([
      onRefresh(),
      queryClient.invalidateQueries({ queryKey: ['lab-quest-graph', questId, questId] }),
      queryClient.invalidateQueries({ queryKey: ['lab-quest-events', questId, questId] }),
      queryClient.invalidateQueries({ queryKey: ['lab-quest-node-trace', questId, questId] }),
      queryClient.invalidateQueries({ queryKey: ['lab-quest-event-payload', questId, questId] }),
      queryClient.invalidateQueries({ queryKey: ['lab-quest-summary', questId, questId] }),
      queryClient.invalidateQueries({ queryKey: ['lab-quest-detail', questId, questId] }),
      queryClient.invalidateQueries({ queryKey: ['lab-overview', questId] }),
      queryClient.invalidateQueries({ queryKey: ['lab-quests', questId] }),
    ])
  }, [clearGraphSelection, onRefresh, queryClient, questId])

  React.useEffect(() => {
    if (!liveCanvasKey) return
    const timer = window.setTimeout(() => {
      void Promise.allSettled([
        queryClient.invalidateQueries({ queryKey: ['lab-quest-graph', questId, questId] }),
        queryClient.invalidateQueries({ queryKey: ['lab-quest-events', questId, questId] }),
        queryClient.invalidateQueries({ queryKey: ['lab-quest-node-trace', questId, questId] }),
        queryClient.invalidateQueries({ queryKey: ['lab-quest-event-payload', questId, questId] }),
        queryClient.invalidateQueries({ queryKey: ['lab-quest-summary', questId, questId] }),
        queryClient.invalidateQueries({ queryKey: ['lab-quest-detail', questId, questId] }),
      ])
    }, 180)
    return () => {
      window.clearTimeout(timer)
    }
  }, [liveCanvasKey, queryClient, questId])

  return (
    <div className="relative h-full min-h-0 overflow-hidden bg-[var(--lab-surface-muted)]">
      <div className="absolute right-4 top-4 z-20 flex max-w-[28rem] flex-col items-end gap-2">
        {error ? (
          <div className="max-w-full rounded-full border border-black/[0.08] bg-white/[0.86] px-3 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur dark:border-white/[0.10] dark:bg-[rgba(18,18,18,0.76)]">
            <span className="break-words">{error}</span>
          </div>
        ) : null}
        <WorkspaceRefreshButton onRefresh={handleRefresh} />
      </div>

      <div className="h-full min-h-0 overflow-hidden">
        <LabSurface
          projectId={questId}
          readOnly
          lockedQuestId={questId}
          immersiveLockedQuest
        />
      </div>
    </div>
  )
}

function QuestTerminalLegacySurface({
  questId,
  onRefresh,
}: {
  questId: string
  onRefresh: () => Promise<void>
}) {
  const {
    sessions,
    connection,
    reload: reloadSessions,
  } = useBashSessionStream({
    projectId: questId,
    enabled: Boolean(questId),
    limit: 40,
  })
  const [selectedBashId, setSelectedBashId] = React.useState<string | null>(null)
  const [logEntries, setLogEntries] = React.useState<BashLogEntry[]>([])
  const [progress, setProgress] = React.useState<BashProgress | null>(null)
  const [logsLoading, setLogsLoading] = React.useState(false)
  const [logsError, setLogsError] = React.useState<string | null>(null)
  const [lastLogSeq, setLastLogSeq] = React.useState<number | null>(null)
  const [stopPending, setStopPending] = React.useState(false)

  const selectedSession = React.useMemo(() => {
    if (!sessions.length) return null
    return sessions.find((session) => session.bash_id === selectedBashId) ?? sessions[0]
  }, [selectedBashId, sessions])

  React.useEffect(() => {
    if (!sessions.length) {
      setSelectedBashId(null)
      return
    }
    if (!selectedBashId || !sessions.some((session) => session.bash_id === selectedBashId)) {
      setSelectedBashId(sessions[0].bash_id)
    }
  }, [selectedBashId, sessions])

  const reloadSelectedLogs = React.useCallback(
    async (bashId: string) => {
      setLogsLoading(true)
      setLogsError(null)
      try {
        const payload = await getBashLogs(questId, bashId, {
          limit: 800,
          order: 'asc',
        })
        setLogEntries(sortBashLogEntries(payload.entries))
        setLastLogSeq(
          payload.meta.latestSeq ??
            payload.entries[payload.entries.length - 1]?.seq ??
            null
        )
      } catch (error) {
        setLogsError(error instanceof Error ? error.message : 'Failed to load terminal logs.')
        setLogEntries([])
        setLastLogSeq(null)
      } finally {
        setLogsLoading(false)
      }
    },
    [questId]
  )

  React.useEffect(() => {
    if (!selectedSession) {
      setProgress(null)
      setLogEntries([])
      setLogsError(null)
      setLastLogSeq(null)
      return
    }
    setProgress(selectedSession.last_progress ?? null)
    void reloadSelectedLogs(selectedSession.bash_id)
  }, [reloadSelectedLogs, selectedSession])

  useBashLogStream({
    projectId: questId,
    bashId: selectedSession?.bash_id ?? null,
    enabled: Boolean(
      selectedSession &&
        (selectedSession.status === 'running' ||
          selectedSession.status === 'terminating')
    ),
    lastEventId: lastLogSeq,
    onSnapshot: (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      const nextEntries = (event.lines || []).map((line) => ({
        seq: line.seq,
        stream: line.stream || 'stdout',
        line: line.line || '',
        timestamp: line.timestamp || '',
      }))
      setLogEntries(sortBashLogEntries(nextEntries))
      setLastLogSeq(event.latest_seq ?? nextEntries[nextEntries.length - 1]?.seq ?? null)
      setProgress(event.progress ?? selectedSession.last_progress ?? null)
    },
    onLogBatch: (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      const nextEntries = (event.lines || []).map((line) => ({
        seq: line.seq,
        stream: line.stream || 'stdout',
        line: line.line || '',
        timestamp: line.timestamp || '',
      }))
      setLogEntries((current) => mergeBashLogEntries(current, nextEntries))
      setLastLogSeq(event.to_seq ?? nextEntries[nextEntries.length - 1]?.seq ?? null)
    },
    onProgress: (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      setProgress(event)
    },
    onGap: (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      void reloadSelectedLogs(event.bash_id)
    },
    onDone: async (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      await Promise.allSettled([reloadSelectedLogs(event.bash_id), reloadSessions(), onRefresh()])
    },
    onError: (error) => {
      setLogsError(error.message)
    },
  })

  const logText = React.useMemo(() => {
    if (!logEntries.length) return ''
    return logEntries
      .map((entry) => {
        const prefix = entry.stream === 'stderr' ? '! ' : ''
        return `${prefix}${entry.line}`
      })
      .join('\n')
  }, [logEntries])

  const handleRefresh = React.useCallback(async () => {
    await Promise.allSettled([
      reloadSessions(),
      selectedSession ? reloadSelectedLogs(selectedSession.bash_id) : Promise.resolve(),
      onRefresh(),
    ])
  }, [onRefresh, reloadSelectedLogs, reloadSessions, selectedSession])

  const handleStop = React.useCallback(async () => {
    if (!selectedSession) return
    setStopPending(true)
    try {
      await stopBashSession(questId, selectedSession.bash_id, 'Stopped from terminal panel')
      await Promise.allSettled([
        reloadSessions(),
        reloadSelectedLogs(selectedSession.bash_id),
        onRefresh(),
      ])
    } finally {
      setStopPending(false)
    }
  }, [onRefresh, questId, reloadSelectedLogs, reloadSessions, selectedSession])

  return (
    <div className="feed-scrollbar h-full overflow-y-auto overflow-x-hidden">
      <div className="mx-auto flex min-h-full max-w-[1120px] flex-col px-5 pb-10 pt-5 sm:px-6 lg:px-8">
        <DetailSection
          first
          title="Terminal"
          hint="Quest-local bash_exec sessions and logs. The latest running session streams here automatically."
          actions={<WorkspaceRefreshButton onRefresh={handleRefresh} label="Refresh terminal" />}
        >
          {!sessions.length ? (
            <div className="rounded-[24px] border border-dashed border-black/[0.10] px-4 py-6 text-sm text-muted-foreground dark:border-white/[0.12]">
              No bash_exec sessions recorded yet.
            </div>
          ) : (
            <div className="grid min-h-[620px] gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
              <div className="rounded-[26px] border border-black/[0.08] bg-[linear-gradient(180deg,rgba(255,255,255,0.86),rgba(244,239,233,0.94))] p-3 shadow-card dark:border-white/[0.10] dark:bg-[linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.03))]">
                <div className="flex items-center justify-between gap-3 px-1 pb-3">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                    Sessions
                  </div>
                  <StatusPill>{formatBashSessionStatus(connection.status)}</StatusPill>
                </div>
                <div className="space-y-2">
                  {sessions.map((session) => {
                    const sessionProgress =
                      getProgressPercent(session.last_progress) ??
                      session.last_progress?.percent ??
                      null
                    const isActive = session.bash_id === selectedSession?.bash_id
                    return (
                      <button
                        key={session.bash_id}
                        type="button"
                        onClick={() => setSelectedBashId(session.bash_id)}
                        className={cn(
                          'w-full rounded-[20px] border px-3 py-3 text-left transition',
                          isActive
                            ? 'border-[#9b8352]/50 bg-[#9b8352]/[0.08] shadow-sm'
                            : 'border-black/[0.06] bg-white/[0.56] hover:border-black/[0.10] hover:bg-white/[0.78] dark:border-white/[0.08] dark:bg-white/[0.03] dark:hover:bg-white/[0.05]'
                        )}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold text-foreground">
                              {session.command || 'bash_exec'}
                            </div>
                            <div className="mt-1 truncate text-xs text-muted-foreground">
                              {session.workdir || 'quest root'}
                            </div>
                          </div>
                          <StatusPill>{formatBashSessionStatus(session.status)}</StatusPill>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                          <span>{formatRelativeTime(session.started_at)}</span>
                          <span>·</span>
                          <span>{session.mode}</span>
                          {typeof sessionProgress === 'number' ? (
                            <>
                              <span>·</span>
                              <span>{sessionProgress.toFixed(0)}%</span>
                            </>
                          ) : null}
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>

              <div className="min-w-0 rounded-[26px] border border-black/[0.08] bg-[linear-gradient(180deg,rgba(255,255,255,0.86),rgba(244,239,233,0.94))] p-4 shadow-card dark:border-white/[0.10] dark:bg-[linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.03))]">
                {selectedSession ? (
                  <>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <div className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-black/[0.04] text-foreground dark:bg-white/[0.06]">
                            <Terminal className="h-4 w-4" />
                          </div>
                          <div>
                            <div className="break-words text-lg font-semibold text-foreground">
                              {selectedSession.command || 'bash_exec'}
                            </div>
                            <div className="mt-1 break-all text-xs text-muted-foreground">
                              {selectedSession.workdir}
                            </div>
                          </div>
                        </div>
                        <div className="mt-4 flex flex-wrap gap-2">
                          <StatusPill>{formatBashSessionStatus(selectedSession.status)}</StatusPill>
                          <StatusPill mono>{selectedSession.bash_id}</StatusPill>
                          <StatusPill>{selectedSession.mode}</StatusPill>
                          {selectedSession.exit_code != null ? (
                            <StatusPill>exit {selectedSession.exit_code}</StatusPill>
                          ) : null}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {selectedSession.status === 'running' ||
                        selectedSession.status === 'terminating' ? (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              void handleStop()
                            }}
                            disabled={stopPending}
                            className="h-9 rounded-full border-black/[0.08] bg-white/[0.84] px-3 text-[11px] shadow-sm backdrop-blur hover:bg-white dark:border-white/[0.10] dark:bg-[rgba(18,18,18,0.72)] dark:hover:bg-[rgba(24,24,24,0.9)]"
                          >
                            <Square className="mr-1.5 h-3.5 w-3.5" />
                            {stopPending ? 'Stopping…' : 'Stop'}
                          </Button>
                        ) : null}
                      </div>
                    </div>

                    <div className="mt-6 grid gap-x-10 gap-y-6 sm:grid-cols-2 xl:grid-cols-4">
                      <OverviewMetric
                        icon={<Activity className="h-4 w-4" />}
                        label="State"
                        value={formatBashSessionStatus(selectedSession.status)}
                        hint={connection.status === 'open' ? 'streaming live' : connection.status}
                      />
                      <OverviewMetric
                        icon={<Clock3 className="h-4 w-4" />}
                        label="Started"
                        value={formatRelativeTime(selectedSession.started_at)}
                        hint={selectedSession.finished_at ? `Finished ${formatRelativeTime(selectedSession.finished_at)}` : 'Still active'}
                      />
                      <OverviewMetric
                        icon={<FlaskConical className="h-4 w-4" />}
                        label="Progress"
                        value={
                          progress && (getProgressPercent(progress) ?? progress.percent) != null
                            ? `${(getProgressPercent(progress) ?? progress.percent ?? 0).toFixed(0)}%`
                            : '—'
                        }
                        hint={progress?.desc || progress?.phase || selectedSession.stop_reason || null}
                      />
                      <OverviewMetric
                        icon={<FileCode2 className="h-4 w-4" />}
                        label="Log path"
                        value={selectedSession.log_path.split('/').slice(-3).join('/')}
                        hint={selectedSession.log_path}
                      />
                    </div>

                    <div className="mt-6 overflow-hidden rounded-[24px] border border-black/[0.10] bg-[#0f1115] shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] dark:border-white/[0.10]">
                      <div className="flex items-center justify-between gap-3 border-b border-white/[0.08] px-4 py-3 text-xs text-white/70">
                        <div className="flex items-center gap-2">
                          <div className="flex items-center gap-1.5" aria-hidden="true">
                            <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
                            <span className="h-2.5 w-2.5 rounded-full bg-[#ffbd2e]" />
                            <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
                          </div>
                          <span>bash_exec</span>
                        </div>
                        <div className="flex items-center gap-2">
                          {logsLoading ? <span>loading…</span> : null}
                          {logsError ? <span className="text-[#ffb4b4]">{logsError}</span> : null}
                        </div>
                      </div>
                      <pre className="feed-scrollbar min-h-[360px] overflow-auto px-4 py-4 text-[12px] leading-6 text-[#d8dee9]">
                        {logText || 'No terminal output yet.'}
                      </pre>
                    </div>
                  </>
                ) : null}
              </div>
            </div>
          )}
        </DetailSection>
      </div>
    </div>
  )
}

function QuestTerminalSurface({
  questId,
  onRefresh,
}: {
  questId: string
  onRefresh: () => Promise<void>
}) {
  const { addToast } = useToast()
  const {
    sessions,
    connection,
    reload: reloadSessions,
  } = useBashSessionStream({
    projectId: questId,
    enabled: Boolean(questId),
    limit: 120,
  })

  const terminalSessions = React.useMemo(
    () => sessions.filter((session) => String(session.kind || '').toLowerCase() === 'terminal'),
    [sessions]
  )
  const [selectedSessionId, setSelectedSessionId] = React.useState<string | null>(null)
  const [logsLoading, setLogsLoading] = React.useState(false)
  const [logsError, setLogsError] = React.useState<string | null>(null)
  const [stopPending, setStopPending] = React.useState(false)
  const [lastSeq, setLastSeq] = React.useState<number | null>(null)
  const lastSeqRef = React.useRef<number | null>(null)
  const [sessionStatus, setSessionStatus] = React.useState<string | null>(null)
  const [exitCode, setExitCode] = React.useState<number | null>(null)
  const [stopReason, setStopReason] = React.useState<string | null>(null)
  const [progress, setProgress] = React.useState<BashProgress | null>(null)

  React.useEffect(() => {
    lastSeqRef.current = lastSeq
  }, [lastSeq])

  const selectedSession = React.useMemo<BashSession | null>(() => {
    if (!terminalSessions.length) return null
    return (
      terminalSessions.find((session) => session.bash_id === selectedSessionId) ??
      terminalSessions[0]
    )
  }, [selectedSessionId, terminalSessions])

  React.useEffect(() => {
    if (!terminalSessions.length) {
      setSelectedSessionId(null)
      return
    }
    if (
      !selectedSessionId ||
      !terminalSessions.some((session) => session.bash_id === selectedSessionId)
    ) {
      setSelectedSessionId(terminalSessions[0].bash_id)
    }
  }, [selectedSessionId, terminalSessions])

  const ensuredRef = React.useRef(false)
  React.useEffect(() => {
    if (!questId || ensuredRef.current) return
    ensuredRef.current = true
    let cancelled = false
    void ensureTerminalSession(questId, { source: 'web-react' })
      .then(({ session }) => {
        if (cancelled) return
        setSelectedSessionId((prev) => prev ?? session.bash_id)
        void reloadSessions()
      })
      .catch((error) => {
        ensuredRef.current = false
        addToast({
          type: 'error',
          title: 'Terminal unavailable',
          description: error instanceof Error ? error.message : 'Unable to start terminal session.',
        })
      })
    return () => {
      cancelled = true
    }
  }, [addToast, questId, reloadSessions])

  React.useEffect(() => {
    setSessionStatus(selectedSession?.status ?? null)
    setExitCode(selectedSession?.exit_code ?? null)
    setStopReason(selectedSession?.stop_reason ?? null)
    setProgress(selectedSession?.last_progress ?? null)
  }, [
    selectedSession?.bash_id,
    selectedSession?.exit_code,
    selectedSession?.last_progress,
    selectedSession?.status,
    selectedSession?.stop_reason,
  ])

  type TerminalHandlers = {
    write: (data: string, onComplete?: () => void) => void
    clear: () => void
    scrollToBottom: () => void
    focus: () => void
    isScrolledToBottom?: (thresholdPx?: number) => boolean
  }

  const terminalHandlersRef = React.useRef<TerminalHandlers | null>(null)
  const pendingOutputRef = React.useRef<string>('')

  const appendToTerminal = React.useCallback((text: string) => {
    const handlers = terminalHandlersRef.current
    if (!handlers) {
      pendingOutputRef.current += text
      return
    }
    const shouldAutoScroll = handlers.isScrolledToBottom?.() ?? true
    handlers.write(text, () => {
      if (shouldAutoScroll) {
        handlers.scrollToBottom()
      }
    })
  }, [])

  const resetTerminal = React.useCallback(() => {
    pendingOutputRef.current = ''
    terminalHandlersRef.current?.clear()
  }, [])

  const handleLogLine = React.useCallback(
    (entry: { line: string; stream?: string | null }) => {
      if (entry.stream === 'prompt') {
        // The terminal process already prints its own prompt (and readline escape sequences).
        // Keep the quest-local cwd in session metadata, but don't duplicate a second prompt line.
        return
      }
      const line = entry.line ?? ''
      if (!line) {
        appendToTerminal('\n')
        return
      }
      if (isBashProgressMarker(line)) {
        return
      }
      const marker = parseBashStatusMarker(line)
      if (marker) {
        setSessionStatus(marker.status)
        setExitCode(marker.exitCode)
        setStopReason(marker.reason)
        return
      }
      const parsed = splitBashLogLine(line)
      if (parsed.kind === 'carriage') {
        appendToTerminal(`\r\x1b[K${parsed.text}`)
        return
      }
      appendToTerminal(`${parsed.text}\n`)
    },
    [appendToTerminal]
  )

  const reloadSelectedLogs = React.useCallback(
    async (bashId: string) => {
      setLogsLoading(true)
      setLogsError(null)
      try {
        const payload = await getBashLogs(questId, bashId, {
          limit: 1000,
          order: 'asc',
        })
        resetTerminal()
        payload.entries.forEach((entry) => handleLogLine(entry))
        const latestSeq =
          payload.meta.latestSeq ?? payload.entries[payload.entries.length - 1]?.seq ?? null
        setLastSeq(typeof latestSeq === 'number' ? latestSeq : null)
      } catch (error) {
        setLogsError(error instanceof Error ? error.message : 'Failed to load terminal logs.')
        setLastSeq(null)
        resetTerminal()
      } finally {
        setLogsLoading(false)
      }
    },
    [handleLogLine, questId, resetTerminal]
  )

  React.useEffect(() => {
    if (!selectedSession) {
      setLogsError(null)
      setLastSeq(null)
      lastSeqRef.current = null
      resetTerminal()
      return
    }
    setLastSeq(null)
    lastSeqRef.current = null
    void reloadSelectedLogs(selectedSession.bash_id)
  }, [reloadSelectedLogs, resetTerminal, selectedSession])

  useBashLogStream({
    projectId: questId,
    bashId: selectedSession?.bash_id ?? null,
    enabled: Boolean(
      selectedSession &&
        (selectedSession.status === 'running' || selectedSession.status === 'terminating')
    ),
    lastEventId: lastSeq ?? undefined,
    onSnapshot: (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      resetTerminal()
      let maxSeq: number | null = typeof event.latest_seq === 'number' ? event.latest_seq : null
      event.lines?.forEach((line) => {
        if (typeof line.seq === 'number') {
          maxSeq = maxSeq == null || line.seq > maxSeq ? line.seq : maxSeq
        }
        handleLogLine({ line: line.line ?? '', stream: line.stream })
      })
      if (event.progress) {
        setProgress(event.progress)
      }
      if (maxSeq != null) {
        setLastSeq(maxSeq)
      }
    },
    onLogBatch: (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      let maxSeq: number | null = null
      event.lines?.forEach((line) => {
        if (typeof line.seq === 'number') {
          if (lastSeqRef.current != null && line.seq <= lastSeqRef.current) {
            return
          }
          maxSeq = maxSeq == null || line.seq > maxSeq ? line.seq : maxSeq
        }
        handleLogLine({ line: line.line ?? '', stream: line.stream })
      })
      if (maxSeq != null) {
        setLastSeq((prev) => (prev == null || maxSeq > prev ? maxSeq : prev))
      }
    },
    onProgress: (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      setProgress(event)
    },
    onGap: (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      void reloadSelectedLogs(event.bash_id)
    },
    onDone: async (event) => {
      if (!selectedSession || event.bash_id !== selectedSession.bash_id) return
      setSessionStatus(event.status)
      if (typeof event.exit_code === 'number') {
        setExitCode(event.exit_code)
      }
      await Promise.allSettled([reloadSessions(), onRefresh()])
    },
    onError: (error) => {
      setLogsError(error.message)
    },
  })

  const handleRefresh = React.useCallback(async () => {
    await Promise.allSettled([
      reloadSessions(),
      selectedSession ? reloadSelectedLogs(selectedSession.bash_id) : Promise.resolve(),
      onRefresh(),
    ])
  }, [onRefresh, reloadSelectedLogs, reloadSessions, selectedSession])

  const handleStop = React.useCallback(async () => {
    if (!selectedSession) return
    setStopPending(true)
    try {
      await stopBashSession(questId, selectedSession.bash_id, 'Stopped from terminal panel')
      await Promise.allSettled([
        reloadSessions(),
        reloadSelectedLogs(selectedSession.bash_id),
        onRefresh(),
      ])
    } finally {
      setStopPending(false)
    }
  }, [onRefresh, questId, reloadSelectedLogs, reloadSessions, selectedSession])

  const handleNewTerminal = React.useCallback(async () => {
    try {
      const response = await ensureTerminalSession(questId, {
        createNew: true,
        label: `terminal-${terminalSessions.length + 1}`,
        source: 'web-react',
      })
      await reloadSessions()
      setSelectedSessionId(response.session.bash_id)
      window.setTimeout(() => {
        terminalHandlersRef.current?.focus()
      }, 80)
    } catch (error) {
      addToast({
        type: 'error',
        title: 'Failed to create terminal',
        description: error instanceof Error ? error.message : 'Unable to create terminal session.',
      })
    }
  }, [addToast, questId, reloadSessions, terminalSessions.length])

  const inputBufferRef = React.useRef<string>('')
  const inputFlushTimerRef = React.useRef<number | null>(null)
  const flushInputRef = React.useRef<(() => void) | null>(null)

  const flushInput = React.useCallback(async () => {
    if (!selectedSession) return
    const pending = inputBufferRef.current
    if (!pending) return
    inputBufferRef.current = ''
    try {
      await sendTerminalInput(questId, selectedSession.bash_id, {
        data: pending,
        source: 'web-react',
      })
    } catch (error) {
      addToast({
        type: 'error',
        title: 'Terminal input failed',
        description: error instanceof Error ? error.message : 'Unable to send input to terminal.',
      })
    }
  }, [addToast, questId, selectedSession])

  React.useEffect(() => {
    flushInputRef.current = () => {
      void flushInput()
    }
  }, [flushInput])

  React.useEffect(() => {
    inputBufferRef.current = ''
    if (inputFlushTimerRef.current != null) {
      window.clearTimeout(inputFlushTimerRef.current)
      inputFlushTimerRef.current = null
    }
  }, [selectedSession?.bash_id])

  React.useEffect(() => {
    return () => {
      if (inputFlushTimerRef.current != null) {
        window.clearTimeout(inputFlushTimerRef.current)
        inputFlushTimerRef.current = null
      }
    }
  }, [])

  const scheduleInputFlush = React.useCallback(
    (data: string) => {
      if (!selectedSession) return
      inputBufferRef.current += data
      const shouldFlushNow = /[\r\n]/.test(data) || inputBufferRef.current.length > 2048
      if (shouldFlushNow) {
        if (inputFlushTimerRef.current != null) {
          window.clearTimeout(inputFlushTimerRef.current)
          inputFlushTimerRef.current = null
        }
        flushInputRef.current?.()
        return
      }
      if (inputFlushTimerRef.current != null) return
      inputFlushTimerRef.current = window.setTimeout(() => {
        inputFlushTimerRef.current = null
        flushInputRef.current?.()
      }, 60)
    },
    [selectedSession]
  )

  return (
    <div className="h-full min-h-0 overflow-hidden bg-[var(--lab-surface-muted)]">
      <div className="flex h-full min-h-0 overflow-hidden">
        <div className="w-[320px] shrink-0 border-r border-black/[0.08] bg-[linear-gradient(180deg,rgba(255,255,255,0.86),rgba(244,239,233,0.94))] p-3 shadow-card dark:border-white/[0.10] dark:bg-[linear-gradient(180deg,rgba(255,255,255,0.06),rgba(255,255,255,0.03))]">
          <div className="flex items-center justify-between gap-3 px-1 pb-3">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
              Terminals
            </div>
            <div className="flex items-center gap-2">
              <StatusPill>{formatBashSessionStatus(connection.status)}</StatusPill>
              <Button
                type="button"
                size="icon"
                variant="outline"
                onClick={() => {
                  void handleNewTerminal()
                }}
                className="h-9 w-9 rounded-full border-black/[0.08] bg-white/[0.84] shadow-sm backdrop-blur hover:bg-white dark:border-white/[0.10] dark:bg-[rgba(18,18,18,0.72)]"
                title="New terminal"
              >
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          </div>

          <div className="feed-scrollbar h-[calc(100%-3.25rem)] space-y-2 overflow-auto pr-1">
            {!terminalSessions.length ? (
              <div className="rounded-[20px] border border-dashed border-black/[0.10] px-3 py-4 text-sm text-muted-foreground dark:border-white/[0.12]">
                No terminal sessions yet.
              </div>
            ) : (
              terminalSessions.map((session) => {
                const isActive = session.bash_id === selectedSession?.bash_id
                return (
                  <button
                    key={session.bash_id}
                    type="button"
                    onClick={() => setSelectedSessionId(session.bash_id)}
                    className={cn(
                      'w-full rounded-[20px] border px-3 py-3 text-left transition',
                      isActive
                        ? 'border-[#9b8352]/50 bg-[#9b8352]/[0.08] shadow-sm'
                        : 'border-black/[0.06] bg-white/[0.56] hover:border-black/[0.10] hover:bg-white/[0.78] dark:border-white/[0.08] dark:bg-white/[0.03] dark:hover:bg-white/[0.05]'
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-foreground">
                          {session.label || session.bash_id}
                        </div>
                        <div className="mt-1 truncate text-xs text-muted-foreground">
                          {session.workdir || 'quest root'}
                        </div>
                      </div>
                      <StatusPill>{formatBashSessionStatus(session.status)}</StatusPill>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                      <span>{formatRelativeTime(session.started_at)}</span>
                      <span>·</span>
                      <span>{session.mode}</span>
                    </div>
                  </button>
                )
              })
            )}
          </div>
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 flex-col gap-3 p-3">
            <div className="flex items-center justify-between gap-3">
              <WorkspaceRefreshButton onRefresh={handleRefresh} label="Refresh terminal" />
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                {logsLoading ? <span>loading…</span> : null}
                {logsError ? (
                  <span className="text-[#b42318] dark:text-[#ffb4b4]">{logsError}</span>
                ) : null}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden rounded-[24px] border border-black/[0.10] bg-[#0f1115] shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] dark:border-white/[0.10]">
              <div className="h-full min-h-0 px-4 py-4">
                <EnhancedTerminal
                  onInput={(data) => {
                    scheduleInputFlush(data)
                  }}
                  onResize={() => {}}
                  onReady={(handlers) => {
                    terminalHandlersRef.current = handlers
                    if (pendingOutputRef.current) {
                      const payload = pendingOutputRef.current
                      pendingOutputRef.current = ''
                      handlers.write(payload, () => {
                        handlers.scrollToBottom()
                      })
                    }
                    window.setTimeout(() => handlers.focus(), 60)
                  }}
                  searchOpen={false}
                  onSearchOpenChange={() => {}}
                  appearance="terminal"
                  autoFocus={false}
                  showHeader={false}
                  scrollback={1000}
                />
              </div>
            </div>
          </div>

          <div className="flex items-center justify-between gap-3 border-t border-black/[0.08] px-4 py-3 text-xs text-muted-foreground dark:border-white/[0.10]">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <StatusPill>
                {formatBashSessionStatus(sessionStatus ?? selectedSession?.status ?? 'idle')}
              </StatusPill>
              {selectedSession?.bash_id ? <StatusPill mono>{selectedSession.bash_id}</StatusPill> : null}
              {selectedSession?.workdir ? <StatusPill mono>{selectedSession.workdir}</StatusPill> : null}
              {progress && (getProgressPercent(progress) ?? progress.percent) != null ? (
                <StatusPill>{`${(getProgressPercent(progress) ?? progress.percent ?? 0).toFixed(0)}%`}</StatusPill>
              ) : null}
              {exitCode != null ? <StatusPill>exit {exitCode}</StatusPill> : null}
              {stopReason ? <span className="truncate">{stopReason}</span> : null}
            </div>
            {selectedSession &&
            (selectedSession.status === 'running' || selectedSession.status === 'terminating') ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => {
                  void handleStop()
                }}
                disabled={stopPending}
                className="h-8 rounded-full border-black/[0.08] bg-white/[0.84] px-3 text-[11px] shadow-sm backdrop-blur hover:bg-white dark:border-white/[0.10] dark:bg-[rgba(18,18,18,0.72)] dark:hover:bg-[rgba(24,24,24,0.9)]"
              >
                <Square className="mr-1.5 h-3.5 w-3.5" />
                {stopPending ? 'Stopping…' : 'Stop'}
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}

function QuestDetails({
  questId,
  snapshot,
  workflow,
  feed,
  documents,
  memory,
  branches,
  loading,
  restoring,
  connectionState,
  onOpenDocument,
  onRefresh,
  error,
}: {
  questId: string
  snapshot: ReturnType<typeof useQuestWorkspace>['snapshot']
  workflow: ReturnType<typeof useQuestWorkspace>['workflow']
  feed: ReturnType<typeof useQuestWorkspace>['feed']
  documents: ReturnType<typeof useQuestWorkspace>['documents']
  memory: ReturnType<typeof useQuestWorkspace>['memory']
  branches: GitBranchesPayload | null
  loading: boolean
  restoring: boolean
  connectionState: ReturnType<typeof useQuestWorkspace>['connectionState']
  onOpenDocument: (item: LinkItem) => void
  onRefresh: () => Promise<void>
  error?: string | null
}) {
  const nodeCount = branches?.nodes.length ?? 0
  const ideaCount =
    branches?.nodes.filter((item) => item.branch_kind === 'idea').length ?? 0
  const analysisCount =
    branches?.nodes.filter(
      (item) => item.branch_kind === 'analysis' || item.mode === 'analysis'
    ).length ?? 0
  const recentFeed = React.useMemo(() => [...feed].slice(-12).reverse(), [feed])
  const { sessions: runningBashSessions } = useBashSessionStream({
    projectId: questId,
    status: 'running',
    enabled: Boolean(questId),
    limit: 50,
  })
  const latestRunningBash = runningBashSessions[0] ?? null
  const [metricsTimeline, setMetricsTimeline] =
    React.useState<MetricsTimelinePayload | null>(null)
  const latestRunningBashHint = React.useMemo(() => {
    if (!latestRunningBash) {
      return `${snapshot?.counts?.bash_session_count || 0} recorded sessions`
    }
    const progressPercent = getProgressPercent(latestRunningBash.last_progress)
    const command =
      latestRunningBash.command?.trim().replace(/\s+/g, ' ') || 'bash_exec'
    const compactCommand =
      command.length > 52 ? `${command.slice(0, 49).trimEnd()}...` : command
    return progressPercent == null
      ? compactCommand
      : `${compactCommand} · ${progressPercent.toFixed(0)}%`
  }, [latestRunningBash, snapshot?.counts?.bash_session_count])

  const changedFiles = React.useMemo<LinkItem[]>(
    () =>
      (workflow?.changed_files ?? []).slice(0, 12).map((item) => ({
        key: `${item.source}:${item.path}`,
        title: item.path,
        subtitle: item.source,
        badge: item.writable === false ? 'read-only' : 'live',
        documentId: item.document_id || `path::${item.path}`,
      })),
    [workflow?.changed_files]
  )

  const coreDocs = React.useMemo<LinkItem[]>(() => {
    const preferred = [
      ['status.md', 'Operational status'],
      ['plan.md', 'Accepted plan'],
      ['SUMMARY.md', 'Quest summary'],
      ['brief.md', 'Original brief'],
    ] as const

    return preferred.map(([path, label]) => {
      const existing =
        documents.find((item) => (item.path || '').endsWith(path)) ??
        documents.find((item) => item.title === path)

      return {
        key: path,
        title: path,
        subtitle: existing?.title && existing.title !== path ? existing.title : label,
        badge: 'core',
        documentId: existing?.document_id || `path::${path}`,
      }
    })
  }, [documents])

  const recentDocs = React.useMemo<LinkItem[]>(
    () =>
      documents.slice(0, 10).map((item) => ({
        key: item.document_id,
        title: item.title,
        subtitle: item.path || item.source_scope || item.kind,
        badge: item.kind,
        documentId: item.document_id,
      })),
    [documents]
  )

  const recentMemory = React.useMemo<LinkItem[]>(
    () =>
      memory.slice(0, 10).map((item, index) => ({
        key: `${item.document_id || item.path || 'memory'}-${index}`,
        title: item.title || item.path || 'Memory',
        subtitle: item.path || item.excerpt || item.type || null,
        badge: item.type || 'memory',
        documentId: item.document_id || null,
      })),
    [memory]
  )

  const recentArtifacts = React.useMemo<LinkItem[]>(
    () =>
      (snapshot?.recent_artifacts || []).slice(0, 8).map((item, index) => ({
        key: `${item.kind}:${item.path}:${index}`,
        title: item.payload?.summary || item.payload?.reason || item.kind,
        subtitle: item.path,
        badge: item.kind,
        documentId: item.path ? `path::${item.path}` : null,
      })),
    [snapshot?.recent_artifacts]
  )

  const recentRuns = React.useMemo<LinkItem[]>(
    () =>
      (snapshot?.recent_runs || []).slice(0, 6).map((item, index) => ({
        key: `${item.run_id || item.skill_id || 'run'}-${index}`,
        title: item.summary || item.skill_id || item.run_id || 'Run',
        subtitle: [
          item.status || null,
          item.model || null,
          item.updated_at ? `updated ${formatRelativeTime(item.updated_at)}` : null,
        ]
          .filter(Boolean)
          .join(' · '),
        badge: item.skill_id || 'run',
        documentId: item.output_path ? `path::${item.output_path}` : null,
      })),
    [snapshot?.recent_runs]
  )

  const guidance = (snapshot?.guidance ?? null) as GuidanceVm | null
  const latestMetric = snapshot?.summary?.latest_metric ?? null
  const statusLine =
    snapshot?.summary?.status_line || 'Research workspace ready.'
  const pendingDecisionCount = snapshot?.counts?.pending_decision_count || 0
  const pendingUserMessages = snapshot?.counts?.pending_user_message_count || 0
  const runningBashCount =
    runningBashSessions.length || snapshot?.counts?.bash_running_count || 0

  React.useEffect(() => {
    let cancelled = false
    void client
      .metricsTimeline(questId)
      .then((payload) => {
        if (!cancelled) {
          setMetricsTimeline(payload)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMetricsTimeline(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [questId, snapshot?.updated_at, workflow?.entries.length])

  return (
    <div className="feed-scrollbar h-full overflow-y-auto overflow-x-hidden">
      <div className="mx-auto flex min-h-full max-w-[1120px] flex-col px-5 pb-10 pt-5 sm:px-6 lg:px-8">
        <DetailSection
          first
          title="Metrics Overview"
          hint="One chart per metric across recorded main experiments, with baseline reference lines."
          actions={<WorkspaceRefreshButton onRefresh={onRefresh} label="Refresh metrics" />}
        >
          {metricsTimeline?.series?.length ? (
            <div className="grid gap-5 xl:grid-cols-2">
              {metricsTimeline.series.map((series) => (
                <MetricTimelineCard
                  key={series.metric_id}
                  series={series}
                  primaryMetricId={metricsTimeline.primary_metric_id}
                />
              ))}
            </div>
          ) : (
            <div className="rounded-[24px] border border-dashed border-black/[0.10] px-4 py-6 text-sm text-muted-foreground dark:border-white/[0.12]">
              Main-experiment charts will appear after the first recorded result.
            </div>
          )}
        </DetailSection>

        <DetailSection
          title="Overall"
          hint={statusLine}
          actions={<WorkspaceRefreshButton onRefresh={onRefresh} />}
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill>{snapshot?.display_status || snapshot?.status || 'idle'}</StatusPill>
            <StatusPill>{snapshot?.branch || 'main'}</StatusPill>
            <StatusPill>{snapshot?.active_anchor || 'baseline'}</StatusPill>
            <StatusPill>{formatConnectionState(connectionState)}</StatusPill>
            <StatusPill mono>{questId}</StatusPill>
          </div>

          <div className="mt-4 break-words text-[28px] font-semibold tracking-[-0.03em] text-foreground">
            {snapshot?.title || questId}
          </div>

          <div className="mt-6 grid gap-x-10 gap-y-6 sm:grid-cols-2 xl:grid-cols-3">
            <OverviewMetric
              icon={<Activity className="h-4 w-4" />}
              label="Status"
              value={snapshot?.display_status || snapshot?.status || 'idle'}
              hint={error || `Updated ${formatRelativeTime(snapshot?.updated_at)}`}
            />
            <OverviewMetric
              icon={<Clock3 className="h-4 w-4" />}
              label="Runtime"
              value={formatDuration(snapshot?.created_at)}
              hint={`Created ${formatRelativeTime(snapshot?.created_at)}`}
            />
            <OverviewMetric
              icon={<GitBranch className="h-4 w-4" />}
              label="Graph"
              value={`${nodeCount} nodes`}
              hint={`${ideaCount} ideas · ${analysisCount} analysis branches`}
            />
            <OverviewMetric
              icon={<FlaskConical className="h-4 w-4" />}
              label="Bash"
              value={runningBashCount ? `${runningBashCount} running` : 'idle'}
              hint={latestRunningBashHint}
            />
            <OverviewMetric
              icon={<Sparkles className="h-4 w-4" />}
              label="Signal"
              value={
                latestMetric?.key
                  ? `${latestMetric.key} · ${latestMetric.value ?? '—'}`
                  : `${pendingDecisionCount} pending decisions`
              }
              hint={
                latestMetric?.delta_vs_baseline != null
                  ? `Δ ${latestMetric.delta_vs_baseline} vs baseline`
                  : 'Awaiting stronger evidence'
              }
            />
            <OverviewMetric
              icon={<FileCode2 className="h-4 w-4" />}
              label="Working set"
              value={`${changedFiles.length} changed files`}
              hint={`${recentDocs.length} docs · ${recentMemory.length} memory · ${recentArtifacts.length} artifacts`}
            />
          </div>
        </DetailSection>

        <DetailSection
          title="Operational Status"
          hint="Details concentrates the same high-signal quest state that a quick /status-style check should expose."
        >
          <div className="grid gap-8 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,0.85fr)]">
            <div className="min-w-0">
              <div className="divide-y divide-dashed divide-black/[0.10] dark:divide-white/[0.10]">
                <div className="grid gap-2 py-3 sm:grid-cols-[150px_minmax(0,1fr)]">
                  <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                    Runtime state
                  </div>
                  <div className="break-words text-sm leading-7 text-foreground">
                    {snapshot?.runtime_status || snapshot?.display_status || snapshot?.status || 'idle'}
                  </div>
                </div>
                <div className="grid gap-2 py-3 sm:grid-cols-[150px_minmax(0,1fr)]">
                  <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                    Pending work
                  </div>
                  <div className="break-words text-sm leading-7 text-foreground">
                    {pendingDecisionCount} pending decisions · {pendingUserMessages} queued user messages
                  </div>
                </div>
                <div className="grid gap-2 py-3 sm:grid-cols-[150px_minmax(0,1fr)]">
                  <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                    Interaction
                  </div>
                  <div className="break-words text-sm leading-7 text-foreground">
                    {snapshot?.active_interaction_id
                      ? `Active interaction ${snapshot.active_interaction_id}`
                      : snapshot?.waiting_interaction_id
                        ? `Waiting on ${snapshot.waiting_interaction_id}`
                        : 'No blocking interaction'}
                  </div>
                </div>
                <div className="grid gap-2 py-3 sm:grid-cols-[150px_minmax(0,1fr)]">
                  <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                    Latest delivery
                  </div>
                  <div className="break-words text-sm leading-7 text-foreground">
                    {snapshot?.last_delivered_at
                      ? `Delivered ${formatRelativeTime(snapshot.last_delivered_at)}`
                      : 'No mailbox delivery recorded yet'}
                  </div>
                </div>
                {snapshot?.stop_reason ? (
                  <div className="grid gap-2 py-3 sm:grid-cols-[150px_minmax(0,1fr)]">
                    <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                      Stop reason
                    </div>
                    <div className="break-words text-sm leading-7 text-foreground">
                      {snapshot.stop_reason}
                    </div>
                  </div>
                ) : null}
                {snapshot?.pending_decisions?.length ? (
                  <div className="grid gap-2 py-3 sm:grid-cols-[150px_minmax(0,1fr)]">
                    <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                      Decision queue
                    </div>
                    <div className="space-y-1.5">
                      {snapshot.pending_decisions.slice(0, 4).map((item) => (
                        <div
                          key={item}
                          className="break-words text-sm leading-7 text-muted-foreground"
                        >
                          {item}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="min-w-0">
              <DocumentListBlock
                title="Core Docs"
                countLabel={`${coreDocs.length} files`}
                items={coreDocs}
                emptyLabel="Core quest files will appear here."
                onOpen={onOpenDocument}
              />
            </div>
          </div>
        </DetailSection>

        <DetailSection
          title="Next Step"
          hint="This section turns the latest durable guidance into a compact execution brief."
        >
          {guidance ? (
            <div className="grid gap-8 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
              <div className="min-w-0">
                <div className="flex items-start gap-3">
                  <div className="mt-1 shrink-0 text-muted-foreground">
                    <Lightbulb className="h-4 w-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="break-words text-base font-semibold text-foreground">
                      {guidance.summary}
                    </div>
                    <div className="mt-3 break-words text-sm leading-7 text-muted-foreground">
                      {guidance.why_now}
                    </div>
                  </div>
                </div>

                {guidance.complete_when?.length ? (
                  <div className="mt-5 border-l border-dashed border-black/[0.12] pl-4 dark:border-white/[0.12]">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                      Complete when
                    </div>
                    <div className="mt-3 space-y-2">
                      {guidance.complete_when.slice(0, 4).map((item) => (
                        <div
                          key={item}
                          className="break-words text-sm leading-7 text-muted-foreground"
                        >
                          {item}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>

              <div className="min-w-0">
                <div className="divide-y divide-dashed divide-black/[0.10] dark:divide-white/[0.10]">
                  <div className="grid gap-2 py-3 sm:grid-cols-[132px_minmax(0,1fr)]">
                    <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                      Recommended
                    </div>
                    <div className="break-words text-sm leading-7 text-foreground">
                      {guidance.recommended_skill} · {guidance.recommended_action}
                    </div>
                  </div>
                  <div className="grid gap-2 py-3 sm:grid-cols-[132px_minmax(0,1fr)]">
                    <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                      Stage status
                    </div>
                    <div className="break-words text-sm leading-7 text-foreground">
                      {guidance.stage_status || 'ready'}
                      {guidance.requires_user_decision ? ' · waiting for user approval' : ''}
                    </div>
                  </div>
                  <div className="grid gap-2 py-3 sm:grid-cols-[132px_minmax(0,1fr)]">
                    <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                      Anchor
                    </div>
                    <div className="break-words text-sm leading-7 text-foreground">
                      {guidance.current_anchor || snapshot?.active_anchor || 'baseline'}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="py-3 text-sm leading-7 text-muted-foreground">
              Durable guidance will appear after the next stage-significant update.
            </div>
          )}
        </DetailSection>

        <DetailSection
          title="Recent Progress"
          hint="Latest quest messages, tool calls, artifacts, and runtime runs in one linear view."
        >
          <div className="grid gap-8 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,0.7fr)]">
            <div className="min-w-0">
              <ActivityTimeline
                items={recentFeed}
                loading={loading}
                restoring={restoring}
                connectionState={connectionState}
              />
            </div>

            <div className="min-w-0">
              <DocumentListBlock
                title="Recent Runs"
                countLabel={recentRuns.length ? `${recentRuns.length} runs` : null}
                items={recentRuns}
                emptyLabel="Recent stage runs will appear here."
                onOpen={onOpenDocument}
              />
            </div>
          </div>
        </DetailSection>

        <DetailSection
          title="Working Set"
          hint="High-frequency quest materials: changed files, documents, memory, and durable artifact outputs."
        >
          <div className="grid gap-8 lg:grid-cols-2">
            <div className="min-w-0 space-y-8">
              <DocumentListBlock
                title="Changed Files"
                countLabel={changedFiles.length ? `${changedFiles.length} files` : null}
                items={changedFiles}
                emptyLabel="Changed files will appear here."
                onOpen={onOpenDocument}
              />
              <DocumentListBlock
                title="Documents"
                countLabel={recentDocs.length ? `${recentDocs.length} docs` : null}
                items={recentDocs}
                emptyLabel="Quest documents will appear here."
                onOpen={onOpenDocument}
              />
            </div>

            <div className="min-w-0 space-y-8">
              <DocumentListBlock
                title="Memory"
                countLabel={recentMemory.length ? `${recentMemory.length} entries` : null}
                items={recentMemory}
                emptyLabel="Memory cards will appear here."
                onOpen={onOpenDocument}
              />
              <DocumentListBlock
                title="Artifacts"
                countLabel={recentArtifacts.length ? `${recentArtifacts.length} items` : null}
                items={recentArtifacts}
                emptyLabel="Artifact summaries will appear here."
                onOpen={onOpenDocument}
              />
            </div>
          </div>
        </DetailSection>
      </div>
    </div>
  )
}

export function QuestWorkspaceSurfaceInner({
  questId,
  safePaddingLeft,
  safePaddingRight,
  overlay,
  view: controlledView,
  onViewChange,
  workspace,
}: QuestWorkspaceSurfaceInnerProps) {
  const {
    snapshot,
    workflow,
    feed,
    documents,
    memory,
    graph,
    loading,
    restoring,
    error,
    refresh,
    connectionState,
  } = workspace
  const [uncontrolledView, setUncontrolledView] =
    React.useState<QuestWorkspaceView>(controlledView ?? 'canvas')
  const [branches, setBranches] = React.useState<GitBranchesPayload | null>(null)
  const setActiveQuest = useLabCopilotStore((state) => state.setActiveQuest)
  const { openFileInTab } = useOpenFile()
  const view = controlledView ?? uncontrolledView

  React.useEffect(() => {
    setActiveQuest(questId)
  }, [questId, setActiveQuest])

  const updateView = React.useCallback(
    (nextView: QuestWorkspaceView) => {
      if (onViewChange) {
        onViewChange(nextView)
        return
      }
      setUncontrolledView(nextView)
    },
    [onViewChange]
  )

  const openDocumentInTab = React.useCallback(
    async (documentId: string) => {
      const node = await openQuestDocumentAsFileNode(questId, documentId)
      await openFileInTab(node, {
        customData: {
          projectId: questId,
        },
      })
    },
    [openFileInTab, questId]
  )

  const refreshWorkspace = React.useCallback(async () => {
    await refresh(false)
  }, [refresh])

  React.useEffect(() => {
    let cancelled = false
    void client
      .gitBranches(questId)
      .then((payload) => {
        if (!cancelled) {
          setBranches(payload)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBranches(null)
        }
      })
    return () => {
      cancelled = true
    }
  }, [graph?.head, questId, workflow?.entries.length])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    const handleViewChange = (event: Event) => {
      const detail = (event as CustomEvent<QuestWorkspaceViewDetail>).detail
      if (!detail || detail.projectId !== questId) {
        return
      }
      updateView(detail.view)
    }
    window.addEventListener(QUEST_WORKSPACE_VIEW_EVENT, handleViewChange as EventListener)
    return () => {
      window.removeEventListener(
        QUEST_WORKSPACE_VIEW_EVENT,
        handleViewChange as EventListener
      )
    }
  }, [questId, updateView])

  if (
    loading &&
    !snapshot &&
    !workflow &&
    documents.length === 0 &&
    memory.length === 0 &&
    view === 'details'
  ) {
    return (
      <div className="panel center-panel morandi-glow ds-stage" style={{ flex: 1 }}>
        <div
          className="ds-stage-safe flex h-full items-center justify-center"
          style={{ paddingLeft: safePaddingLeft, paddingRight: safePaddingRight }}
        >
          <div className="text-sm text-muted-foreground">
            {restoring ? 'Restoring quest workspace…' : 'Loading quest workspace…'}
          </div>
        </div>
        {overlay}
      </div>
    )
  }

  return (
    <div className="panel center-panel morandi-glow ds-stage" style={{ flex: 1 }}>
      <div
        className={cn(
          'ds-stage-safe h-full min-h-0',
          view === 'canvas' ? 'overflow-hidden' : 'flex flex-col overflow-hidden'
        )}
        style={{ paddingLeft: safePaddingLeft, paddingRight: safePaddingRight }}
      >
        {view === 'canvas' ? (
          <QuestCanvasSurface
            questId={questId}
            feed={feed}
            error={error}
            onRefresh={refreshWorkspace}
          />
        ) : view === 'terminal' ? (
          <QuestTerminalSurface questId={questId} onRefresh={refreshWorkspace} />
        ) : (
          <QuestDetails
            questId={questId}
            snapshot={snapshot}
            workflow={workflow}
            feed={feed}
            documents={documents}
            memory={memory}
            branches={branches}
            loading={loading}
            restoring={restoring}
            connectionState={connectionState}
            onOpenDocument={(item) => {
              if (!item.documentId) return
              void openDocumentInTab(item.documentId)
            }}
            onRefresh={refreshWorkspace}
            error={error}
          />
        )}
      </div>
      {overlay}
    </div>
  )
}

export function QuestWorkspaceSurface(
  props: Omit<QuestWorkspaceSurfaceInnerProps, 'workspace'>
) {
  const workspace = useQuestWorkspace(props.questId)
  return <QuestWorkspaceSurfaceInner {...props} workspace={workspace} />
}

export default QuestWorkspaceSurface
