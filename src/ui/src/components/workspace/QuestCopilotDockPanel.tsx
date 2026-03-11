'use client'

import * as React from 'react'

import { SegmentedControl, type SegmentedItem } from '@/components/ui/segmented-control'
import { useQuestWorkspace } from '@/lib/acp'
import type {
  AiManusChatMeta,
  CopilotPrefill,
} from '@/lib/plugins/ai-manus/view-types'
import { useI18n } from '@/lib/i18n/useI18n'

import { QuestConnectorChatView } from './QuestConnectorChatView'
import { useCopilotDockCallbacks } from './CopilotDockOverlay'
import { QuestStudioTraceView } from './QuestStudioTraceView'

type QuestCopilotDockPanelProps = {
  questId: string
  title: string
  readOnly?: boolean
  prefill?: CopilotPrefill | null
}

type QuestCopilotMode = 'chat' | 'studio'

function resolveStatusText(args: {
  loading: boolean
  restoring: boolean
  error?: string | null
  streaming: boolean
  activeToolCount: number
  connectionState: 'connecting' | 'connected' | 'reconnecting' | 'error'
  snapshotStatus?: string | null
  t: (key: string, variables?: Record<string, string | number>, fallback?: string) => string
}) {
  const { loading, restoring, error, streaming, activeToolCount, connectionState, snapshotStatus, t } = args
  if (error) return error
  if (snapshotStatus) return snapshotStatus
  if (restoring) return t('copilot_quest_status_restoring')
  if (loading) return t('copilot_quest_status_loading')
  if (streaming) {
    return activeToolCount > 0
      ? t('copilot_quest_status_working_tools', { count: activeToolCount })
      : t('copilot_quest_status_working')
  }
  if (connectionState === 'reconnecting') return t('copilot_quest_status_reconnecting')
  if (connectionState === 'connecting') return t('copilot_quest_status_connecting')
  if (connectionState === 'error') return t('copilot_quest_status_interrupted')
  return t('copilot_quest_status_ready')
}

export function QuestCopilotDockPanel({
  questId,
  title,
  readOnly: _readOnly,
  prefill: _prefill,
}: QuestCopilotDockPanelProps) {
  const { t } = useI18n('workspace')
  const dockCallbacks = useCopilotDockCallbacks()
  const workspace = useQuestWorkspace(questId)
  const storageKey = React.useMemo(() => `ds:quest:${questId}:copilot-mode`, [questId])
  const [mode, setMode] = React.useState<QuestCopilotMode>(() => {
    if (typeof window === 'undefined') return 'chat'
    const stored = window.localStorage.getItem(storageKey)
    return stored === 'studio' ? 'studio' : 'chat'
  })

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    const stored = window.localStorage.getItem(storageKey)
    setMode(stored === 'studio' ? 'studio' : 'chat')
  }, [storageKey])

  React.useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(storageKey, mode)
  }, [mode, storageKey])

  React.useEffect(() => {
    dockCallbacks?.onActionsChange(null)
  }, [dockCallbacks, mode])

  const statusText = React.useMemo(
    () =>
      resolveStatusText({
        loading: workspace.loading,
        restoring: workspace.restoring,
        error: workspace.error,
        streaming: workspace.streaming,
        activeToolCount: workspace.activeToolCount,
        connectionState: workspace.connectionState,
        snapshotStatus: workspace.snapshot?.summary?.status_line ?? null,
        t,
      }),
    [
      workspace.activeToolCount,
      workspace.connectionState,
      workspace.error,
      workspace.loading,
      workspace.restoring,
      workspace.snapshot?.summary?.status_line,
      workspace.streaming,
      t,
    ]
  )

  React.useEffect(() => {
    const meta: AiManusChatMeta = {
      threadId: `quest:${questId}:${mode}`,
      historyOpen: false,
      isResponding: workspace.streaming,
      ready: !workspace.loading,
      isRestoring: workspace.restoring,
      restoreAttempted: true,
      hasHistory: workspace.feed.length > 0,
      error: workspace.error ?? null,
      title,
      statusText,
      statusPrevText: null,
      statusKey: workspace.feed.length,
      toolPanelVisible: false,
      toolToggleVisible: false,
      attachmentsDrawerOpen: false,
      fixWithAiRunning: false,
    }
    dockCallbacks?.onMetaChange(meta)
  }, [
    dockCallbacks,
    mode,
    questId,
    statusText,
    title,
    workspace.error,
    workspace.feed.length,
    workspace.loading,
    workspace.restoring,
    workspace.streaming,
  ])

  const tabItems = React.useMemo<SegmentedItem<QuestCopilotMode>[]>(
    () => [
      { value: 'chat', label: t('copilot_chat_tab') },
      { value: 'studio', label: t('copilot_studio_tab') },
    ],
    [t]
  )

  React.useEffect(() => {
    dockCallbacks?.onHeaderExtraChange(
      <SegmentedControl
        value={mode}
        onValueChange={setMode}
        items={tabItems}
        size="sm"
        ariaLabel={t('copilot_mode_tabs')}
        className="quest-copilot-mode-tabs border-black/[0.08] bg-white/[0.62] backdrop-blur-sm dark:border-white/[0.10] dark:bg-white/[0.06]"
      />
    )
    return () => {
      dockCallbacks?.onHeaderExtraChange(null)
    }
  }, [dockCallbacks, mode, t, tabItems])

  return (
    <div className="flex h-full min-h-0 flex-col">
      {mode === 'chat' ? (
        <QuestConnectorChatView
          feed={workspace.feed}
          loading={workspace.loading}
          restoring={workspace.restoring}
          streaming={workspace.streaming}
          activeToolCount={workspace.activeToolCount}
          connectionState={workspace.connectionState}
          error={workspace.error}
          slashCommands={workspace.slashCommands}
          onSubmit={workspace.submit}
          onStopRun={workspace.stopRun}
        />
      ) : (
        <QuestStudioTraceView
          questId={questId}
          feed={workspace.feed}
          loading={workspace.loading}
          restoring={workspace.restoring}
          streaming={workspace.streaming}
          activeToolCount={workspace.activeToolCount}
          connectionState={workspace.connectionState}
          error={workspace.error}
          slashCommands={workspace.slashCommands}
          onSubmit={workspace.submit}
          onStopRun={workspace.stopRun}
        />
      )}
    </div>
  )
}

export default QuestCopilotDockPanel
