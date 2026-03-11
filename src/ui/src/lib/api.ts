import type {
  ConfigFileEntry,
  ConfigTestPayload,
  ConfigValidationPayload,
  ConnectorSnapshot,
  ExplorerPayload,
  FeedEnvelope,
  GitBranchesPayload,
  GitCommitDetailPayload,
  GitComparePayload,
  GitDiffPayload,
  GitLogPayload,
  GraphPayload,
  MemoryCard,
  MetricsTimelinePayload,
  OpenDocumentPayload,
  QuestSearchPayload,
  QuestDocumentAssetUploadPayload,
  QuestNodeTraceDetailPayload,
  QuestNodeTraceListPayload,
  QuestArtifactListPayload,
  QuestRawEventListPayload,
  QuestDocument,
  QuestSummary,
  SessionPayload,
  WorkflowPayload,
} from '@/types'

type ConfigStructuredPayload = Record<string, unknown>

type ConfigSaveInput =
  | { content: string; revision?: string }
  | { structured: ConfigStructuredPayload; revision?: string }

type ConfigValidateInput =
  | { content: string }
  | { structured: ConfigStructuredPayload }

type ConfigTestInput =
  | { content: string; live?: boolean; delivery_targets?: Record<string, unknown> }
  | { structured: ConfigStructuredPayload; live?: boolean; delivery_targets?: Record<string, unknown> }

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await response.text())
  }
  return (await response.json()) as T
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
    },
    ...init,
  })
  return parseResponse<T>(response)
}

export const client = {
  quests: () => api<QuestSummary[]>('/api/quests'),
  session: (questId: string) => api<SessionPayload>(`/api/quests/${questId}/session`),
  updateQuestSettings: (
    questId: string,
    payload: {
      title?: string
      active_anchor?: string
      default_runner?: string
    }
  ) =>
    api<{ ok: boolean; snapshot: QuestSummary }>(`/api/quests/${questId}/settings`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  events: (questId: string, after: number, options?: { limit?: number; tail?: boolean }) => {
    const params = new URLSearchParams()
    params.set('after', String(after))
    params.set('format', 'acp')
    params.set('session_id', `quest:${questId}`)
    if (typeof options?.limit === 'number' && Number.isFinite(options.limit) && options.limit > 0) {
      params.set('limit', String(Math.floor(options.limit)))
    }
    if (options?.tail) {
      params.set('tail', '1')
    }
    return api<FeedEnvelope>(`/api/quests/${questId}/events?${params.toString()}`)
  },
  eventsStreamUrl: (questId: string, after = 0) =>
    `/api/quests/${questId}/events?after=${after}&format=acp&session_id=quest:${questId}&stream=1`,
  workflow: (questId: string) => api<WorkflowPayload>(`/api/quests/${questId}/workflow`),
  rawEvents: (
    questId: string,
    options?: {
      after?: number
      limit?: number
      tail?: boolean
    }
  ) => {
    const params = new URLSearchParams()
    params.set('format', 'raw')
    if (typeof options?.after === 'number' && Number.isFinite(options.after) && options.after >= 0) {
      params.set('after', String(Math.floor(options.after)))
    }
    if (typeof options?.limit === 'number' && Number.isFinite(options.limit) && options.limit > 0) {
      params.set('limit', String(Math.floor(options.limit)))
    }
    if (options?.tail) {
      params.set('tail', '1')
    }
    const suffix = params.toString()
    return api<QuestRawEventListPayload>(`/api/quests/${questId}/events${suffix ? `?${suffix}` : ''}`)
  },
  artifacts: (questId: string) => api<QuestArtifactListPayload>(`/api/quests/${questId}/artifacts`),
  nodeTraces: (questId: string, selectionType?: string | null) =>
    api<QuestNodeTraceListPayload>(
      `/api/quests/${questId}/node-traces${
        selectionType ? `?selection_type=${encodeURIComponent(selectionType)}` : ''
      }`
    ),
  nodeTrace: (questId: string, selectionRef: string, selectionType?: string | null) =>
    api<QuestNodeTraceDetailPayload>(
      `/api/quests/${questId}/node-traces/${encodeURIComponent(selectionRef)}${
        selectionType ? `?selection_type=${encodeURIComponent(selectionType)}` : ''
      }`
    ),
  explorer: (questId: string, options?: { revision?: string | null; mode?: string | null }) => {
    const params = new URLSearchParams()
    if (options?.revision) {
      params.set('revision', options.revision)
    }
    if (options?.mode) {
      params.set('mode', options.mode)
    }
    const suffix = params.toString()
    return api<ExplorerPayload>(`/api/quests/${questId}/explorer${suffix ? `?${suffix}` : ''}`)
  },
  search: (questId: string, query: string, limit = 50) =>
    api<QuestSearchPayload>(
      `/api/quests/${questId}/search?q=${encodeURIComponent(query)}&limit=${Math.max(1, Math.floor(limit))}`
    ),
  memory: (questId: string) => api<MemoryCard[]>(`/api/quests/${questId}/memory`),
  documents: (questId: string) => api<QuestDocument[]>(`/api/quests/${questId}/documents`),
  graph: (questId: string) => api<GraphPayload>(`/api/quests/${questId}/graph`),
  metricsTimeline: (questId: string) => api<MetricsTimelinePayload>(`/api/quests/${questId}/metrics/timeline`),
  gitBranches: (questId: string) => api<GitBranchesPayload>(`/api/quests/${questId}/git/branches`),
  gitLog: (questId: string, ref: string, base?: string, limit = 30) =>
    api<GitLogPayload>(
      `/api/quests/${questId}/git/log?ref=${encodeURIComponent(ref)}${base ? `&base=${encodeURIComponent(base)}` : ''}&limit=${limit}`
    ),
  gitCompare: (questId: string, base: string, head: string) =>
    api<GitComparePayload>(
      `/api/quests/${questId}/git/compare?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`
    ),
  gitCommit: (questId: string, sha: string) =>
    api<GitCommitDetailPayload>(`/api/quests/${questId}/git/commit?sha=${encodeURIComponent(sha)}`),
  gitDiffFile: (questId: string, base: string, head: string, path: string) =>
    api<GitDiffPayload>(
      `/api/quests/${questId}/git/diff-file?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}&path=${encodeURIComponent(path)}`
    ),
  gitCommitFile: (questId: string, sha: string, path: string) =>
    api<GitDiffPayload>(`/api/quests/${questId}/git/commit-file?sha=${encodeURIComponent(sha)}&path=${encodeURIComponent(path)}`),
  openDocument: (questId: string, documentId: string) =>
    api<OpenDocumentPayload>(`/api/quests/${questId}/documents/open`, {
      method: 'POST',
      body: JSON.stringify({ document_id: documentId }),
    }),
  saveDocument: (questId: string, documentId: string, content: string, revision?: string) =>
    api<{
      ok: boolean
      conflict?: boolean
      message?: string
      revision?: string
      updated_payload?: OpenDocumentPayload
    }>(`/api/quests/${questId}/documents/${documentId}`, {
      method: 'PUT',
      body: JSON.stringify({ content, revision }),
    }),
  uploadDocumentAsset: (
    questId: string,
    payload: {
      document_id: string
      file_name: string
      mime_type?: string
      kind?: string
      content_base64: string
    }
  ) =>
    api<QuestDocumentAssetUploadPayload>(`/api/quests/${questId}/documents/assets`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  sendChat: (
    questId: string,
    text: string,
    replyToInteractionId?: string | null,
    clientMessageId?: string | null
  ) =>
    api<{
      ok: boolean
      message?: {
        id?: string
        role?: string
        content?: string
        source?: string
        created_at?: string
        delivery_state?: string
        client_message_id?: string
      }
    }>(`/api/quests/${questId}/chat`, {
      method: 'POST',
      body: JSON.stringify({
        text,
        source: 'web-react',
        reply_to_interaction_id: replyToInteractionId || undefined,
        client_message_id: clientMessageId || undefined,
      }),
    }),
  sendCommand: (questId: string, command: string) =>
    api<Record<string, unknown>>(`/api/quests/${questId}/commands`, {
      method: 'POST',
      body: JSON.stringify({ command, source: 'web-react' }),
    }),
  runSkill: (questId: string, payload: { skill_id: string; model?: string; message: string }) =>
    api<Record<string, unknown>>(`/api/quests/${questId}/runs`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  createQuest: (goal: string) =>
    api<{ ok: boolean; snapshot: QuestSummary }>('/api/quests', {
      method: 'POST',
      body: JSON.stringify({ goal }),
    }),
  createQuestWithOptions: (payload: { goal: string; title?: string; quest_id?: string }) =>
    api<{ ok: boolean; snapshot: QuestSummary }>('/api/quests', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  docsIndex: () => api<QuestDocument[]>('/api/docs'),
  openSystemDoc: (documentId: string) =>
    api<OpenDocumentPayload>('/api/docs/open', {
      method: 'POST',
      body: JSON.stringify({ document_id: documentId }),
    }),
  connectors: () => api<ConnectorSnapshot[]>('/api/connectors'),
  configFiles: () => api<ConfigFileEntry[]>('/api/config/files'),
  configDocument: (name: string) => api<OpenDocumentPayload>(`/api/config/${name}`),
  saveConfig: (name: string, input: ConfigSaveInput) =>
    api<{
      ok: boolean
      conflict?: boolean
      message?: string
      revision?: string
      warnings?: string[]
      errors?: string[]
    }>(`/api/config/${name}`, {
      method: 'PUT',
      body: JSON.stringify({ ...input }),
    }),
  validateConfig: (name: string, input: ConfigValidateInput) =>
    api<ConfigValidationPayload>('/api/config/validate', {
      method: 'POST',
      body: JSON.stringify({ name, ...input }),
    }),
  testConfig: (name: string, input: ConfigTestInput) =>
    api<ConfigTestPayload>('/api/config/test', {
      method: 'POST',
      body: JSON.stringify({ name, ...input }),
    }),
}
