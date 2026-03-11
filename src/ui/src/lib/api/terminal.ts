import { apiClient } from '@/lib/api/client'
import type { BashSession } from '@/lib/types/bash'

export type EnsureTerminalSessionResponse = {
  ok: boolean
  session: BashSession
}

export async function ensureTerminalSession(
  projectId: string,
  input?: {
    bashId?: string
    label?: string
    cwd?: string
    createNew?: boolean
    source?: string
    conversationId?: string
    userId?: string
  }
) {
  const response = await apiClient.post<EnsureTerminalSessionResponse>(
    `/api/quests/${projectId}/terminal/session/ensure`,
    {
      bash_id: input?.bashId,
      label: input?.label,
      cwd: input?.cwd,
      create_new: input?.createNew,
      source: input?.source,
      conversation_id: input?.conversationId,
      user_id: input?.userId,
    }
  )
  return response.data
}

export async function sendTerminalInput(
  projectId: string,
  sessionId: string,
  input: {
    data: string
    source?: string
    conversationId?: string
    userId?: string
  }
) {
  const response = await apiClient.post(`/api/quests/${projectId}/terminal/sessions/${sessionId}/input`, {
    data: input.data,
    source: input.source,
    conversation_id: input.conversationId,
    user_id: input.userId,
  })
  return response.data as Record<string, unknown>
}

