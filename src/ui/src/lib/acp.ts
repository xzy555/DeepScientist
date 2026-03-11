import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { client } from '@/lib/api'
import { extractArtifactComment, extractOperationComment, extractOperationMonitorFields } from '@/lib/agentComment'
import { buildToolOperationContent, extractToolSubject } from '@/lib/toolOperations'
import type {
  ExplorerPayload,
  FeedItem,
  GraphPayload,
  MemoryCard,
  OpenDocumentPayload,
  QuestDocument,
  QuestSummary,
  SessionPayload,
  WorkflowPayload,
} from '@/types'

const MAX_PENDING_ITEMS = 18
const RESTORE_EVENT_LIMIT = 400
const FULL_HISTORY_MAX_BATCHES = 25
const MAX_FEED_HISTORY = RESTORE_EVENT_LIMIT * FULL_HISTORY_MAX_BATCHES
const LOCAL_USER_SOURCE = 'web-local'

type ParsedEvent = {
  id?: string
  event: string
  data: string
}

function safeRandomUUID() {
  if (typeof globalThis !== 'undefined') {
    const cryptoApi = globalThis.crypto as Crypto | undefined
    if (cryptoApi && typeof cryptoApi.randomUUID === 'function') {
      return cryptoApi.randomUUID()
    }
  }

  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const rand = (Math.random() * 16) | 0
    const value = char === 'x' ? rand : (rand & 0x3) | 0x8
    return value.toString(16)
  })
}

function buildId(prefix: string, value?: string) {
  return `${prefix}:${value || safeRandomUUID()}`
}

function parseEventBlock(block: string): ParsedEvent | null {
  const lines = block.split(/\n/)
  let eventType = ''
  let eventId = ''
  const dataLines: string[] = []
  for (const line of lines) {
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('id:')) {
      eventId = line.slice(3).trim()
      continue
    }
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim()
      continue
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }
  if (dataLines.length === 0) return null
  return { id: eventId || undefined, event: eventType || 'message', data: dataLines.join('\n') }
}

function stringifyToolPayload(value: unknown) {
  if (value == null) return undefined
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function normalizeMetadata(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  return value as Record<string, unknown>
}

function normalizeUpdate(raw: Record<string, unknown>): FeedItem {
  const eventType = String(raw.event_type ?? '')
  const data = (raw.data ?? {}) as Record<string, unknown>
  const toolLabel =
    eventType === 'runner.tool_call' || data.label === 'tool_call'
      ? 'tool_call'
      : eventType === 'runner.tool_result' || data.label === 'tool_result'
        ? 'tool_result'
        : null

  if (toolLabel) {
    const toolName = typeof data.tool_name === 'string' ? data.tool_name : undefined
    const args = stringifyToolPayload(data.args)
    const output = stringifyToolPayload(data.output)
    const metadata = normalizeMetadata(data.metadata)
    const mcpServer = typeof data.mcp_server === 'string' ? data.mcp_server : undefined
    const mcpTool = typeof data.mcp_tool === 'string' ? data.mcp_tool : undefined
    const subject = extractToolSubject(toolName, args, output)
    const comment = extractOperationComment({ args, output, metadata })
    const monitorFields = extractOperationMonitorFields({ metadata, comment })
    return {
      id: buildId('operation', String(raw.event_id ?? raw.created_at ?? safeRandomUUID())),
      type: 'operation',
      label: toolLabel,
      content: buildToolOperationContent(toolLabel, toolName, args, output),
      toolName,
      toolCallId: typeof data.tool_call_id === 'string' ? data.tool_call_id : undefined,
      status: typeof data.status === 'string' ? data.status : undefined,
      subject,
      args,
      output,
      createdAt: String(raw.created_at ?? ''),
      mcpServer,
      mcpTool,
      comment,
      monitorPlanSeconds: monitorFields.monitorPlanSeconds,
      monitorStepIndex: monitorFields.monitorStepIndex,
      nextCheckAfterSeconds: monitorFields.nextCheckAfterSeconds,
      metadata: metadata
        ? {
            ...metadata,
            ...(mcpServer ? { mcp_server: mcpServer } : {}),
            ...(mcpTool ? { mcp_tool: mcpTool } : {}),
          }
        : mcpServer || mcpTool
          ? {
              ...(mcpServer ? { mcp_server: mcpServer } : {}),
              ...(mcpTool ? { mcp_tool: mcpTool } : {}),
            }
          : undefined,
    }
  }

  const kind = raw.kind
  if (kind === 'message') {
    const message = (raw.message ?? {}) as Record<string, unknown>
    const isReasoning = eventType === 'runner.reasoning'
    return {
      id: buildId('message', String(raw.event_id ?? raw.created_at ?? safeRandomUUID())),
      type: 'message',
      role: String(message.role ?? 'assistant') === 'user' ? 'user' : 'assistant',
      source: message.source ? String(message.source) : undefined,
      content: String(message.content ?? ''),
      createdAt: String(raw.created_at ?? ''),
      stream: isReasoning ? false : Boolean(message.stream),
      runId: message.run_id ? String(message.run_id) : null,
      skillId: message.skill_id ? String(message.skill_id) : null,
      reasoning: isReasoning,
      eventType: eventType || null,
      clientMessageId: message.client_message_id ? String(message.client_message_id) : null,
      deliveryState: message.delivery_state ? String(message.delivery_state) : null,
    }
  }

  if (kind === 'artifact') {
    const artifact = (raw.artifact ?? {}) as Record<string, unknown>
    return {
      id: buildId('artifact', String(raw.event_id ?? raw.created_at ?? safeRandomUUID())),
      type: 'artifact',
      artifactId: artifact.artifact_id ? String(artifact.artifact_id) : undefined,
      kind: String(artifact.kind ?? 'artifact'),
      status: artifact.status ? String(artifact.status) : undefined,
      content: String(
        artifact.summary ?? artifact.reason ?? artifact.guidance ?? artifact.kind ?? 'Artifact updated.'
      ),
      reason: artifact.reason ? String(artifact.reason) : undefined,
      guidance: artifact.guidance ? String(artifact.guidance) : undefined,
      createdAt: String(raw.created_at ?? ''),
      paths: (artifact.paths as Record<string, string> | undefined) ?? {},
      artifactPath: artifact.artifact_path ? String(artifact.artifact_path) : undefined,
      workspaceRoot: artifact.workspace_root ? String(artifact.workspace_root) : undefined,
      branch: artifact.branch ? String(artifact.branch) : undefined,
      headCommit: artifact.head_commit ? String(artifact.head_commit) : undefined,
      flowType: artifact.flow_type ? String(artifact.flow_type) : undefined,
      protocolStep: artifact.protocol_step ? String(artifact.protocol_step) : undefined,
      ideaId: artifact.idea_id ? String(artifact.idea_id) : null,
      campaignId: artifact.campaign_id ? String(artifact.campaign_id) : null,
      sliceId: artifact.slice_id ? String(artifact.slice_id) : null,
      details:
        artifact.details && typeof artifact.details === 'object' && !Array.isArray(artifact.details)
          ? (artifact.details as Record<string, unknown>)
          : undefined,
      checkpoint:
        artifact.checkpoint && typeof artifact.checkpoint === 'object' && !Array.isArray(artifact.checkpoint)
          ? (artifact.checkpoint as Record<string, unknown>)
          : null,
      attachments: Array.isArray(artifact.attachments)
        ? (artifact.attachments.filter(
            (item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item)
          ) as Array<Record<string, unknown>>)
        : [],
      interactionId: artifact.interaction_id ? String(artifact.interaction_id) : null,
      expectsReply: Boolean(artifact.expects_reply),
      replyMode: artifact.reply_mode ? String(artifact.reply_mode) : null,
      comment: extractArtifactComment(artifact),
    }
  }

  return {
    id: buildId('event', String(raw.event_id ?? raw.created_at ?? safeRandomUUID())),
    type: 'event',
    label: String(data.label ?? raw.event_type ?? 'event'),
    content: String(data.summary ?? data.run_id ?? raw.event_type ?? 'Event updated.'),
    createdAt: String(raw.created_at ?? ''),
  }
}

type MessageFeedItem = Extract<FeedItem, { type: 'message' }>

type FeedState = {
  history: FeedItem[]
  pending: FeedItem[]
}

export type QuestConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'error'

function appendHistoryItem(history: FeedItem[], item: FeedItem): FeedItem[] {
  if (history.some((existing) => existing.id === item.id)) {
    return history
  }
  return [...history, item].slice(-MAX_FEED_HISTORY)
}

function removeMatchingLocalPendingUser(pending: FeedItem[], item: MessageFeedItem): FeedItem[] {
  if (item.role !== 'user') {
    return pending
  }
  let removed = false
  return pending.filter((candidate) => {
    if (removed) {
      return true
    }
    if (
      candidate.type === 'message' &&
      candidate.role === 'user' &&
      candidate.source === LOCAL_USER_SOURCE &&
      ((item.clientMessageId &&
        candidate.clientMessageId &&
        item.clientMessageId === candidate.clientMessageId) ||
        candidate.content === item.content)
    ) {
      removed = true
      return false
    }
    return true
  })
}

function upsertPendingAssistant(pending: FeedItem[], item: MessageFeedItem): FeedItem[] {
  const next = [...pending]
  const matchIndex = next.findIndex(
    (candidate) =>
      candidate.type === 'message' &&
      candidate.role === 'assistant' &&
      candidate.stream &&
      candidate.runId &&
      candidate.runId === item.runId
  )
  if (matchIndex >= 0) {
    const current = next[matchIndex]
    if (current.type === 'message') {
      next[matchIndex] = {
        ...current,
        content: `${current.content}${item.content}`,
        createdAt: item.createdAt || current.createdAt,
        skillId: item.skillId || current.skillId,
        source: item.source || current.source,
      }
    }
    return next.slice(-MAX_PENDING_ITEMS)
  }
  return [...next, item].slice(-MAX_PENDING_ITEMS)
}

function flushPendingAssistant(
  pending: FeedItem[],
  item: MessageFeedItem
): { pending: FeedItem[]; finalized: MessageFeedItem } {
  if (item.role !== 'assistant' || !item.runId) {
    return { pending, finalized: item }
  }
  let pendingText = ''
  const nextPending = pending.filter((candidate) => {
    if (
      candidate.type === 'message' &&
      candidate.role === 'assistant' &&
      candidate.runId &&
      candidate.runId === item.runId
    ) {
      pendingText = candidate.content
      return false
    }
    return true
  })
  return {
    pending: nextPending,
    finalized: item.content
      ? item
      : {
          ...item,
          content: pendingText,
        },
  }
}

function applyIncomingFeedUpdates(state: FeedState, incoming: FeedItem[]): FeedState {
  let nextHistory = [...state.history]
  let nextPending = [...state.pending]
  for (const item of incoming) {
    if (item.type === 'message' && item.reasoning) {
      nextHistory = appendHistoryItem(nextHistory, item)
      continue
    }
    if (item.type === 'message' && item.role === 'assistant' && item.stream) {
      nextPending = upsertPendingAssistant(nextPending, item)
      continue
    }
    if (item.type === 'message' && item.role === 'assistant' && item.runId) {
      const flushed = flushPendingAssistant(nextPending, item)
      nextPending = flushed.pending
      nextHistory = appendHistoryItem(nextHistory, flushed.finalized)
      continue
    }
    if (item.type === 'message' && item.role === 'user') {
      nextPending = removeMatchingLocalPendingUser(nextPending, item)
      nextHistory = appendHistoryItem(nextHistory, item)
      continue
    }
    nextHistory = appendHistoryItem(nextHistory, item)
  }
  return {
    history: nextHistory,
    pending: nextPending,
  }
}

function createLocalUserFeedItem(content: string, clientMessageId: string): FeedItem {
  return {
    id: buildId('local-user', `${Date.now()}-${safeRandomUUID()}`),
    type: 'message',
    role: 'user',
    content,
    source: LOCAL_USER_SOURCE,
    createdAt: new Date().toISOString(),
    clientMessageId,
    deliveryState: 'sending',
  }
}

function shouldRefreshWorkflow(item: FeedItem) {
  return item.type === 'artifact' || item.type === 'event' || item.type === 'operation'
}

function shouldRefreshSessionSnapshot(item: FeedItem) {
  if (item.type !== 'event') return false
  return (
    item.label === 'run_started' ||
    item.label === 'run_finished' ||
    item.label === 'runner.turn_error' ||
    item.label === 'quest.control'
  )
}

function findReplyTargetId(feed: FeedItem[]) {
  for (let index = feed.length - 1; index >= 0; index -= 1) {
    const item = feed[index]
    if (item.type !== 'artifact') continue
    if (item.replyMode === 'blocking' || item.expectsReply) {
      return item.interactionId || item.id
    }
    if (item.replyMode === 'threaded' && item.interactionId) {
      return item.interactionId
    }
  }
  return null
}

function countActiveToolCalls(feed: FeedItem[]) {
  const pending = new Set<string>()
  for (const item of feed) {
    if (item.type !== 'operation' || !item.toolCallId) continue
    if (item.label === 'tool_call') {
      pending.add(item.toolCallId)
      continue
    }
    pending.delete(item.toolCallId)
  }
  return pending.size
}

function snapshotIndicatesLiveRun(snapshot?: QuestSummary | null) {
  if (!snapshot) return false
  const runtimeStatus = String(snapshot.runtime_status ?? snapshot.status ?? '')
    .trim()
    .toLowerCase()
  if (runtimeStatus === 'stopped' || runtimeStatus === 'paused') return false
  if (snapshot.active_run_id) return true
  if (runtimeStatus === 'running') return true
  const bashRunningCount =
    typeof snapshot.counts?.bash_running_count === 'number'
      ? snapshot.counts.bash_running_count
      : 0
  return bashRunningCount > 0
}

function dropPendingAssistantStreams(pending: FeedItem[]) {
  return pending.filter(
    (item) => !(item.type === 'message' && item.role === 'assistant' && item.stream)
  )
}

export function useQuestWorkspace(questId: string | null) {
  const [snapshot, setSnapshot] = useState<QuestSummary | null>(null)
  const [session, setSession] = useState<SessionPayload | null>(null)
  const [memory, setMemory] = useState<MemoryCard[]>([])
  const [documents, setDocuments] = useState<QuestDocument[]>([])
  const [graph, setGraph] = useState<GraphPayload | null>(null)
  const [workflow, setWorkflow] = useState<WorkflowPayload | null>(null)
  const [explorer, setExplorer] = useState<ExplorerPayload | null>(null)
  const [history, setHistory] = useState<FeedItem[]>([])
  const [pendingFeed, setPendingFeed] = useState<FeedItem[]>([])
  const [loading, setLoading] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [historyTruncated, setHistoryTruncated] = useState(false)
  const [historyLimit, setHistoryLimit] = useState<number | null>(null)
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [historyLoadingFull, setHistoryLoadingFull] = useState(false)
  const [connectionState, setConnectionState] = useState<QuestConnectionState>('connecting')
  const [error, setError] = useState<string | null>(null)
  const [activeDocument, setActiveDocument] = useState<OpenDocumentPayload | null>(null)
  const cursorRef = useRef(0)
  const questIdRef = useRef<string | null>(questId)
  const streamAbortRef = useRef<AbortController | null>(null)
  const streamReconnectRef = useRef<number | null>(null)
  const historyRef = useRef<FeedItem[]>([])
  const pendingFeedRef = useRef<FeedItem[]>([])
  const operationalRefreshTimerRef = useRef<number | null>(null)
  const operationalRefreshInFlightRef = useRef(false)
  const operationalRefreshPendingRef = useRef(false)
  const pendingStreamCleanupTimerRef = useRef<number | null>(null)
  const lastEventIdRef = useRef<string | null>(null)

  const feed = useMemo(() => [...history, ...pendingFeed], [history, pendingFeed])
  const slashCommands = useMemo(() => session?.acp_session?.slash_commands ?? [], [session])
  const replyTargetId = useMemo(() => {
    const snapshotTarget =
      snapshot?.default_reply_interaction_id ||
      session?.acp_session?.meta?.default_reply_interaction_id
    return snapshotTarget || findReplyTargetId(feed)
  }, [feed, session, snapshot])
  const hasLiveRun = useMemo(() => {
    const currentSnapshot = session?.snapshot ?? snapshot
    return snapshotIndicatesLiveRun(currentSnapshot)
  }, [session, snapshot])
  const streaming = useMemo(
    () =>
      hasLiveRun &&
      pendingFeed.some(
        (item) => item.type === 'message' && item.role === 'assistant' && item.stream
      ),
    [hasLiveRun, pendingFeed]
  )
  const activeToolCount = useMemo(
    () => (hasLiveRun ? countActiveToolCalls(feed.slice(-180)) : 0),
    [feed, hasLiveRun]
  )

  const updateFeedState = useCallback((nextState: FeedState) => {
    historyRef.current = nextState.history
    pendingFeedRef.current = nextState.pending
    setHistory(nextState.history)
    setPendingFeed(nextState.pending)
  }, [])

  const hydrateState = useCallback(async (targetQuestId: string) => {
    const [nextSession, nextMemory, nextDocuments, nextGraph, nextWorkflow, nextExplorer] =
      await Promise.all([
        client.session(targetQuestId),
        client.memory(targetQuestId),
        client.documents(targetQuestId),
        client.graph(targetQuestId),
        client.workflow(targetQuestId),
        client.explorer(targetQuestId),
      ])
    if (questIdRef.current !== targetQuestId) {
      return null
    }
    setSession(nextSession)
    setSnapshot(nextSession.snapshot)
    setMemory(nextMemory)
    setDocuments(nextDocuments)
    setGraph(nextGraph)
    setWorkflow(nextWorkflow)
    setExplorer(nextExplorer)
    return nextSession
  }, [])

  const syncSessionSnapshot = useCallback(async (targetQuestId: string) => {
    const nextSession = await client.session(targetQuestId)
    if (questIdRef.current !== targetQuestId) {
      return null
    }
    setSession(nextSession)
    setSnapshot(nextSession.snapshot)
    return nextSession
  }, [])

  const clearOperationalRefresh = useCallback(() => {
    if (operationalRefreshTimerRef.current) {
      window.clearTimeout(operationalRefreshTimerRef.current)
      operationalRefreshTimerRef.current = null
    }
    operationalRefreshPendingRef.current = false
  }, [])

  const clearPendingStreamCleanup = useCallback(() => {
    if (pendingStreamCleanupTimerRef.current) {
      window.clearTimeout(pendingStreamCleanupTimerRef.current)
      pendingStreamCleanupTimerRef.current = null
    }
  }, [])

  const schedulePendingStreamCleanup = useCallback(() => {
    clearPendingStreamCleanup()
    pendingStreamCleanupTimerRef.current = window.setTimeout(() => {
      pendingStreamCleanupTimerRef.current = null
      const nextPending = dropPendingAssistantStreams(pendingFeedRef.current)
      if (nextPending.length === pendingFeedRef.current.length) {
        return
      }
      updateFeedState({
        history: historyRef.current,
        pending: nextPending,
      })
    }, 1400)
  }, [clearPendingStreamCleanup, updateFeedState])

  const stopEventStream = useCallback(() => {
    if (streamAbortRef.current) {
      streamAbortRef.current.abort()
      streamAbortRef.current = null
    }
    if (streamReconnectRef.current) {
      window.clearTimeout(streamReconnectRef.current)
      streamReconnectRef.current = null
    }
  }, [])

  const refreshOperationalViews = useCallback(async (targetQuestId: string) => {
    const [nextWorkflow, nextExplorer] = await Promise.all([
      client.workflow(targetQuestId),
      client.explorer(targetQuestId),
    ])
    if (questIdRef.current !== targetQuestId) {
      return
    }
    setWorkflow(nextWorkflow)
    setExplorer(nextExplorer)
  }, [])

  const flushOperationalRefresh = useCallback(
    async (targetQuestId: string) => {
      if (questIdRef.current !== targetQuestId) {
        return
      }
      if (operationalRefreshInFlightRef.current) {
        operationalRefreshPendingRef.current = true
        return
      }
      operationalRefreshInFlightRef.current = true
      try {
        await refreshOperationalViews(targetQuestId)
      } catch (caught) {
        if (questIdRef.current === targetQuestId) {
          setError(caught instanceof Error ? caught.message : String(caught))
        }
      } finally {
        operationalRefreshInFlightRef.current = false
        if (operationalRefreshPendingRef.current && questIdRef.current === targetQuestId) {
          operationalRefreshPendingRef.current = false
          window.setTimeout(() => {
            void flushOperationalRefresh(targetQuestId)
          }, 180)
        }
      }
    },
    [refreshOperationalViews]
  )

  const queueOperationalRefresh = useCallback(
    (targetQuestId: string, delay = 260) => {
      if (questIdRef.current !== targetQuestId) {
        return
      }
      if (operationalRefreshTimerRef.current) {
        operationalRefreshPendingRef.current = true
        return
      }
      operationalRefreshTimerRef.current = window.setTimeout(() => {
        operationalRefreshTimerRef.current = null
        void flushOperationalRefresh(targetQuestId)
      }, delay)
    },
    [flushOperationalRefresh]
  )

  const applyUpdates = useCallback(
    async (targetQuestId: string, updates: Array<Record<string, unknown>>) => {
      if (questIdRef.current !== targetQuestId || updates.length === 0) {
        return
      }
      const normalized = updates.map((item) => normalizeUpdate(item))
      const nextState = applyIncomingFeedUpdates(
        {
          history: historyRef.current,
          pending: pendingFeedRef.current,
        },
        normalized
      )
      updateFeedState(nextState)
      if (normalized.some((item) => shouldRefreshSessionSnapshot(item))) {
        const nextSession = await syncSessionSnapshot(targetQuestId)
        if (snapshotIndicatesLiveRun(nextSession?.snapshot ?? null)) {
          clearPendingStreamCleanup()
        } else if (
          nextState.pending.some(
            (item) => item.type === 'message' && item.role === 'assistant' && item.stream
          )
        ) {
          schedulePendingStreamCleanup()
        }
      }
      if (normalized.some((item) => item.type === 'message' && item.role === 'assistant' && !item.stream)) {
        clearPendingStreamCleanup()
      }
      if (normalized.some((item) => item.type === 'message' && item.stream && item.role === 'assistant')) {
        queueOperationalRefresh(targetQuestId)
      }
      if (normalized.some((item) => shouldRefreshWorkflow(item) && item.type !== 'artifact')) {
        clearOperationalRefresh()
        await flushOperationalRefresh(targetQuestId)
      }
      if (normalized.some((item) => item.type === 'artifact')) {
        clearOperationalRefresh()
        await hydrateState(targetQuestId)
      }
    },
    [
      clearOperationalRefresh,
      clearPendingStreamCleanup,
      flushOperationalRefresh,
      hydrateState,
      queueOperationalRefresh,
      schedulePendingStreamCleanup,
      syncSessionSnapshot,
      updateFeedState,
    ]
  )

  const bootstrap = useCallback(
    async (reset = false) => {
      if (!questId) {
        return
      }
      const targetQuestId = questId
      setLoading(true)
      if (reset) {
        setRestoring(true)
        setConnectionState('connecting')
        cursorRef.current = 0
        lastEventIdRef.current = null
        setHistoryTruncated(false)
        setHistoryLimit(null)
        setHistoryExpanded(false)
        setHistoryLoadingFull(false)
        updateFeedState({ history: [], pending: [] })
      }
      try {
        const after = reset ? 0 : cursorRef.current
        const [hydrated, nextFeed] = await Promise.all([
          hydrateState(targetQuestId),
          reset
            ? client.events(targetQuestId, 0, { limit: RESTORE_EVENT_LIMIT, tail: true })
            : client.events(targetQuestId, after),
        ])
        if (!hydrated || questIdRef.current !== targetQuestId) {
          return
        }

        const normalized = (nextFeed.acp_updates ?? []).map((item) => normalizeUpdate(item.params.update))
        const baseState: FeedState = reset
          ? { history: [], pending: [] }
          : { history: historyRef.current, pending: pendingFeedRef.current }
        const nextState = applyIncomingFeedUpdates(baseState, normalized)

        if (hydrated.snapshot?.status && hydrated.snapshot.status !== 'running') {
          nextState.pending = nextState.pending.filter(
            (item) => !(item.type === 'message' && item.role === 'assistant' && item.stream)
          )
        }

        updateFeedState(nextState)
        cursorRef.current = typeof nextFeed.cursor === 'number' ? nextFeed.cursor : after
        lastEventIdRef.current = String(cursorRef.current)
        if (reset) {
          const truncated = Boolean(nextFeed.has_more)
          setHistoryTruncated(truncated)
          setHistoryLimit(truncated ? normalized.length : null)
        }
        setError(null)
        setConnectionState('connected')
      } catch (caught) {
        if (questIdRef.current === targetQuestId) {
          setError(caught instanceof Error ? caught.message : String(caught))
          setConnectionState('error')
        }
      } finally {
        if (questIdRef.current === targetQuestId) {
          setLoading(false)
          setRestoring(false)
        }
      }
    },
    [hydrateState, questId, updateFeedState]
  )

  const loadFullHistory = useCallback(async () => {
    if (!questId || historyLoadingFull) {
      return
    }
    const targetQuestId = questId
    setHistoryLoadingFull(true)
    setError(null)
    try {
      const hydrated = await hydrateState(targetQuestId)
      if (!hydrated || questIdRef.current !== targetQuestId) {
        return
      }

      let after = 0
      let batches = 0
      let lastCursor = 0
      let hasMore = true
      const aggregated: FeedItem[] = []

      while (hasMore && batches < FULL_HISTORY_MAX_BATCHES) {
        const response = await client.events(targetQuestId, after, { limit: RESTORE_EVENT_LIMIT })
        if (questIdRef.current !== targetQuestId) {
          return
        }
        const normalized = (response.acp_updates ?? []).map((item) => normalizeUpdate(item.params.update))
        aggregated.push(...normalized)
        lastCursor = typeof response.cursor === 'number' ? response.cursor : after
        hasMore = Boolean(response.has_more)
        after = lastCursor
        batches += 1
        if ((response.acp_updates ?? []).length === 0) {
          break
        }
      }

      const nextState = applyIncomingFeedUpdates(
        {
          history: [],
          pending: [],
        },
        aggregated
      )

      if (hydrated.snapshot?.status && hydrated.snapshot.status !== 'running') {
        nextState.pending = nextState.pending.filter(
          (item) => !(item.type === 'message' && item.role === 'assistant' && item.stream)
        )
      }

      updateFeedState(nextState)
      cursorRef.current = lastCursor
      lastEventIdRef.current = String(lastCursor)
      setHistoryTruncated(hasMore)
      setHistoryLimit(hasMore ? aggregated.length : null)
      setHistoryExpanded(!hasMore)
    } catch (caught) {
      if (questIdRef.current === targetQuestId) {
        setError(caught instanceof Error ? caught.message : String(caught))
      }
    } finally {
      if (questIdRef.current === targetQuestId) {
        setHistoryLoadingFull(false)
      }
    }
  }, [historyLoadingFull, hydrateState, questId, updateFeedState])

  const submit = useCallback(
    async (value: string) => {
      const trimmed = value.trim()
      if (!trimmed || !questId) {
        return
      }
      setError(null)
      if (trimmed.startsWith('/')) {
        await client.sendCommand(questId, trimmed)
        await bootstrap(false)
        return
      }

      const clientMessageId = safeRandomUUID()
      const localUserItem = createLocalUserFeedItem(trimmed, clientMessageId)
      updateFeedState({
        history: historyRef.current,
        pending: [...pendingFeedRef.current, localUserItem].slice(-MAX_PENDING_ITEMS),
      })
      clearPendingStreamCleanup()

      try {
        const response = await client.sendChat(questId, trimmed, replyTargetId, clientMessageId)
        const nextDeliveryState =
          response?.message?.delivery_state ? String(response.message.delivery_state) : 'sent'
        updateFeedState({
          history: historyRef.current,
          pending: pendingFeedRef.current.map((item) =>
            item.id === localUserItem.id && item.type === 'message'
              ? { ...item, deliveryState: nextDeliveryState }
              : item
          ),
        })
      } catch (caught) {
        updateFeedState({
          history: historyRef.current,
          pending: pendingFeedRef.current.map((item) =>
            item.id === localUserItem.id && item.type === 'message'
              ? { ...item, deliveryState: 'failed' }
              : item
          ),
        })
        throw caught
      }
    },
    [bootstrap, clearPendingStreamCleanup, questId, replyTargetId, updateFeedState]
  )

  const stopRun = useCallback(async () => {
    if (!questId) return
    await client.sendCommand(questId, '/stop')
    await bootstrap(false)
  }, [bootstrap, questId])

  useEffect(() => {
    questIdRef.current = questId
  }, [questId])

  useEffect(() => {
    setSnapshot(null)
    setSession(null)
    setMemory([])
    setDocuments([])
    setGraph(null)
    setWorkflow(null)
    setExplorer(null)
    setHistory([])
    setPendingFeed([])
    historyRef.current = []
    pendingFeedRef.current = []
    cursorRef.current = 0
    setHistoryTruncated(false)
    setHistoryLimit(null)
    setHistoryExpanded(false)
    setHistoryLoadingFull(false)
    setConnectionState(questId ? 'connecting' : 'connected')
    setError(null)
    clearOperationalRefresh()
    clearPendingStreamCleanup()
    operationalRefreshInFlightRef.current = false
    stopEventStream()
    lastEventIdRef.current = null
    if (!questId) {
      return
    }
    setRestoring(true)
    void bootstrap(true)
  }, [bootstrap, clearOperationalRefresh, clearPendingStreamCleanup, questId, stopEventStream])

  const runEventStream = useCallback(
    async (targetQuestId: string, attempt = 0) => {
      if (!targetQuestId || questIdRef.current !== targetQuestId) {
        return
      }
      stopEventStream()
      const controller = new AbortController()
      streamAbortRef.current = controller
      setConnectionState(attempt > 0 ? 'reconnecting' : 'connecting')

      try {
        const headers: Record<string, string> = {
          Accept: 'text/event-stream',
        }
        if (lastEventIdRef.current) {
          headers['Last-Event-ID'] = lastEventIdRef.current
        }
        const response = await fetch(client.eventsStreamUrl(targetQuestId, cursorRef.current), {
          method: 'GET',
          headers,
          signal: controller.signal,
        })

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`)
        }
        if (!response.body) {
          throw new Error('No event stream body')
        }

        if (questIdRef.current === targetQuestId) {
          setError(null)
          setConnectionState('connected')
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

          let boundaryIndex = buffer.indexOf('\n\n')
          while (boundaryIndex !== -1) {
            const raw = buffer.slice(0, boundaryIndex)
            buffer = buffer.slice(boundaryIndex + 2)
            const parsed = parseEventBlock(raw.trim())
            if (parsed?.id) {
              lastEventIdRef.current = parsed.id
            }
            if (parsed?.event === 'acp_update') {
              const payload = JSON.parse(parsed.data) as { params?: { update?: Record<string, unknown> } }
              const update = payload.params?.update
              if (update) {
                const nextCursor = Number(update.cursor ?? cursorRef.current)
                if (Number.isFinite(nextCursor)) {
                  cursorRef.current = nextCursor
                  lastEventIdRef.current = String(nextCursor)
                }
                await applyUpdates(targetQuestId, [update])
              }
            } else if (parsed?.event === 'cursor') {
              const payload = JSON.parse(parsed.data) as { cursor?: number }
              if (typeof payload.cursor === 'number') {
                cursorRef.current = payload.cursor
                lastEventIdRef.current = String(payload.cursor)
              }
            }
            boundaryIndex = buffer.indexOf('\n\n')
          }
        }

        if (!controller.signal.aborted && questIdRef.current === targetQuestId) {
          const nextAttempt = 1
          const delay = Math.min(1000 * 2 ** Math.min(nextAttempt, 5), 30000)
          setConnectionState('reconnecting')
          setError('Event stream reconnecting…')
          streamReconnectRef.current = window.setTimeout(() => {
            void runEventStream(targetQuestId, nextAttempt)
          }, delay)
        }
      } catch (caught) {
        if (controller.signal.aborted) {
          return
        }
        if (questIdRef.current === targetQuestId) {
          setConnectionState(attempt > 0 ? 'reconnecting' : 'error')
          setError('Event stream reconnecting…')
          const nextAttempt = attempt + 1
          const delay = Math.min(1000 * 2 ** Math.min(nextAttempt, 5), 30000)
          streamReconnectRef.current = window.setTimeout(() => {
            void runEventStream(targetQuestId, nextAttempt)
          }, delay)
        }
      } finally {
        if (streamAbortRef.current === controller) {
          streamAbortRef.current = null
        }
      }
    },
    [applyUpdates, stopEventStream]
  )

  useEffect(() => {
    if (!questId || restoring) {
      return
    }
    const targetQuestId = questId
    void runEventStream(targetQuestId, 0)
    return () => {
      stopEventStream()
      clearOperationalRefresh()
      clearPendingStreamCleanup()
    }
  }, [clearOperationalRefresh, clearPendingStreamCleanup, questId, restoring, runEventStream, stopEventStream])

  return {
    snapshot,
    session,
    memory,
    documents,
    graph,
    workflow,
    explorer,
    feed,
    history,
    pendingFeed,
    loading,
    restoring,
    historyTruncated,
    historyLimit,
    historyExpanded,
    historyLoadingFull,
    streaming,
    activeToolCount,
    connectionState,
    error,
    slashCommands,
    activeDocument,
    replyTargetId,
    setActiveDocument,
    refresh: (reset = true) => bootstrap(reset),
    loadFullHistory,
    submit,
    stopRun,
  }
}
