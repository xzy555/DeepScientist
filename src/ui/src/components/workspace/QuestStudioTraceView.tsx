'use client'

import * as React from 'react'
import { ArrowUp, Loader2, Slash, Square } from 'lucide-react'

import { EventFeed } from '@/components/EventFeed'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useToast } from '@/components/ui/toast'
import { useI18n } from '@/lib/i18n/useI18n'
import type { FeedItem } from '@/types'

type ConnectorCommand = {
  name: string
  description?: string
}

type QuestStudioTraceViewProps = {
  questId: string
  feed: FeedItem[]
  loading: boolean
  restoring: boolean
  streaming: boolean
  activeToolCount: number
  connectionState: 'connecting' | 'connected' | 'reconnecting' | 'error'
  error?: string | null
  slashCommands?: ConnectorCommand[]
  onSubmit: (message: string) => Promise<void>
  onStopRun: () => Promise<void>
}

export function QuestStudioTraceView({
  questId,
  feed,
  loading,
  restoring,
  streaming,
  activeToolCount,
  connectionState,
  error,
  slashCommands = [],
  onSubmit,
  onStopRun,
}: QuestStudioTraceViewProps) {
  const { t } = useI18n('workspace')
  const { addToast } = useToast()
  const [input, setInput] = React.useState('')
  const [submitting, setSubmitting] = React.useState(false)
  const composerRef = React.useRef<HTMLTextAreaElement | null>(null)

  const filteredCommands = React.useMemo(() => {
    const raw = input.trimStart()
    if (!raw.startsWith('/')) return []
    const query = raw.slice(1).toLowerCase()
    return slashCommands
      .filter((item) => {
        if (!query) return true
        return (
          item.name.toLowerCase().includes(query) ||
          (item.description || '').toLowerCase().includes(query)
        )
      })
      .slice(0, 8)
  }, [input, slashCommands])

  const handleSubmit = React.useCallback(async () => {
    const trimmed = input.trim()
    if (!trimmed || submitting) return
    setSubmitting(true)
    try {
      await onSubmit(trimmed)
      setInput('')
      composerRef.current?.focus()
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught)
      addToast({
        title: t('copilot_send_failed_title', undefined, 'Send failed'),
        message,
        variant: 'error',
      })
    } finally {
      setSubmitting(false)
    }
  }, [addToast, input, onSubmit, submitting, t])

  const statusLine = React.useMemo(() => {
    if (error) return error
    if (connectionState !== 'connected') return connectionState
    if (streaming) {
      return activeToolCount > 0 ? `working · ${activeToolCount} tools` : 'working'
    }
    return ''
  }, [activeToolCount, connectionState, error, streaming])

  return (
    <div className="flex h-full min-h-0 flex-col">
      {statusLine ? (
        <div className="px-4 pt-3 text-[11px] text-muted-foreground">{statusLine}</div>
      ) : null}

      <div className="flex-1 min-h-0 px-4 py-4">
        <EventFeed
          questId={questId}
          items={feed}
          loading={loading}
          restoring={restoring}
          connectionState={connectionState}
          emptyLabel={t('copilot_studio_empty', undefined, 'Copilot trace appears here.')}
        />
      </div>

      <div className="border-t border-black/[0.06] bg-white/[0.35] px-4 py-3 backdrop-blur-sm dark:border-white/[0.08] dark:bg-white/[0.03]">
        <div className="relative">
          {filteredCommands.length > 0 ? (
            <div className="absolute bottom-full left-0 right-0 mb-2 overflow-hidden rounded-2xl border border-black/[0.08] bg-white/[0.92] shadow-[0_18px_42px_-34px_rgba(17,24,39,0.22)] dark:border-white/[0.10] dark:bg-[rgba(34,37,44,0.92)]">
              {filteredCommands.map((item) => (
                <button
                  key={item.name}
                  type="button"
                  className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm transition hover:bg-black/[0.03] dark:hover:bg-white/[0.05]"
                  onClick={() => {
                    setInput(`/${item.name} `)
                    composerRef.current?.focus()
                  }}
                >
                  <Slash className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="font-medium">/{item.name}</span>
                  {item.description ? (
                    <span className="ml-auto line-clamp-1 text-xs text-muted-foreground">
                      {item.description}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
          ) : null}

          <Textarea
            ref={composerRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if ((event.nativeEvent as any)?.isComposing) return
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void handleSubmit()
              }
            }}
            rows={2}
            className="min-h-[56px] resize-none rounded-2xl border border-black/[0.08] bg-white/[0.9] px-4 py-3 shadow-sm focus-visible:ring-0 dark:border-white/[0.10] dark:bg-white/[0.05]"
            placeholder={t('copilot_connector_placeholder')}
          />

          <div className="mt-2 flex items-center justify-between gap-3">
            <div className="text-[11px] text-muted-foreground">{t('copilot_connector_enter_hint')}</div>
            <div className="flex items-center gap-2">
              {streaming ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-8 rounded-full"
                  onClick={() => void onStopRun()}
                >
                  <Square className="mr-2 h-3.5 w-3.5" />
                  {t('copilot_stop')}
                </Button>
              ) : null}
              <Button
                type="button"
                size="sm"
                className="h-8 rounded-full"
                disabled={!input.trim() || submitting}
                onClick={() => void handleSubmit()}
              >
                {submitting ? (
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ArrowUp className="mr-2 h-3.5 w-3.5" />
                )}
                {t('copilot_send')}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default QuestStudioTraceView
