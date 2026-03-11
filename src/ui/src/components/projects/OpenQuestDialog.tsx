import { FolderOpen, Loader2, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { OverlayDialog } from '@/components/home/OverlayDialog'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { useI18n } from '@/lib/i18n'
import type { QuestSummary } from '@/types'

function formatTime(value?: string, locale?: string) {
  if (!value) {
    return '...'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(parsed)
}

export function OpenQuestDialog({
  open,
  quests,
  loading,
  error,
  onClose,
  onOpenQuest,
}: {
  open: boolean
  quests: QuestSummary[]
  loading: boolean
  error?: string | null
  onClose: () => void
  onOpenQuest: (questId: string) => void
}) {
  const { locale, t } = useI18n()
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (!open) {
      setSearch('')
    }
  }, [open])

  const filteredQuests = useMemo(() => {
    const keyword = search.trim().toLowerCase()
    if (!keyword) {
      return quests
    }
    return quests.filter((quest) =>
      `${quest.title} ${quest.quest_id} ${quest.branch || ''} ${quest.summary?.status_line || ''}`
        .toLowerCase()
        .includes(keyword)
    )
  }, [quests, search])

  return (
    <OverlayDialog
      open={open}
      title={t('openQuestTitle')}
      description={t('openQuestBody')}
      onClose={onClose}
      className="h-[min(92vh,760px)] max-w-4xl"
    >
      <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] gap-0 md:grid-cols-[280px_minmax(0,1fr)] md:grid-rows-1">
        <aside className="feed-scrollbar min-h-0 overflow-auto border-b border-black/[0.06] px-5 py-5 dark:border-[rgba(45,42,38,0.08)] md:border-b-0 md:border-r">
          <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
            <FolderOpen className="h-4 w-4 text-muted-foreground" />
            {t('openQuestTitle')}
          </div>

          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t('openQuestSearchPlaceholder')}
              className="pl-10"
            />
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <Badge>{t('landingQuestCount')}: {quests.length}</Badge>
            {quests[0]?.updated_at ? <Badge>{t('openQuestLatest')}: {formatTime(quests[0].updated_at, locale)}</Badge> : null}
          </div>
        </aside>

        <div className="feed-scrollbar min-h-0 overflow-y-auto px-4 py-4 sm:px-5">
          {loading ? (
            <div className="flex min-h-[420px] items-center justify-center gap-3 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('loading')}
            </div>
          ) : error ? (
            <div className="rounded-[24px] border border-rose-500/20 bg-rose-500/8 px-4 py-4 text-sm text-rose-700 dark:text-rose-300">
              {error}
            </div>
          ) : filteredQuests.length === 0 ? (
            <div className="flex min-h-[420px] items-center justify-center rounded-[26px] border border-dashed border-black/[0.10] text-sm text-muted-foreground dark:border-white/[0.12]">
              {t('openQuestEmpty')}
            </div>
          ) : (
            <div className="space-y-3">
              {filteredQuests.map((quest) => {
                const pendingCount = quest.pending_decisions?.length ?? quest.counts?.pending_decision_count ?? 0
                return (
                  <button
                    key={quest.quest_id}
                    type="button"
                    onClick={() => onOpenQuest(quest.quest_id)}
                    className="home-card w-full rounded-[28px] px-5 py-4 text-left"
                  >
                    <div className="relative z-[1]">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0">
                          <div className="truncate text-base font-semibold tracking-tight">{quest.title || quest.quest_id}</div>
                          <div className="mt-1 truncate text-sm text-muted-foreground">{quest.summary?.status_line || t('openQuestNoDescription')}</div>
                        </div>
                        <div className="shrink-0 text-xs text-muted-foreground">
                          {t('openQuestUpdated')}: {formatTime(quest.updated_at, locale)}
                        </div>
                      </div>

                      <div className="mt-3 flex flex-wrap gap-2">
                        <Badge>{quest.quest_id}</Badge>
                        {quest.branch ? <Badge>{t('openQuestBranch')}: {quest.branch}</Badge> : null}
                        {pendingCount > 0 ? <Badge>{t('openQuestPending')}: {pendingCount}</Badge> : null}
                      </div>

                      <div className="mt-4 inline-flex rounded-full border border-black/[0.08] bg-white/[0.72] px-3 py-1.5 text-xs font-medium text-[rgba(38,36,33,0.9)] dark:border-black/[0.08] dark:bg-white/[0.78] dark:text-[rgba(38,36,33,0.9)]">
                        {t('landingOpen')}
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </OverlayDialog>
  )
}
