import { useState } from 'react'
import { FolderOpen, Plus, Sparkles } from 'lucide-react'

import CountUp from '@/components/react-bits/CountUp'
import { FadeContent, GlareHover, Noise, SpotlightCard } from '@/components/react-bits'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/lib/i18n'
import { cn } from '@/lib/utils'

export function ProjectsHero({
  projectCount,
  pendingCount,
  loading,
  creating,
  error,
  onCreate,
  onOpen,
}: {
  projectCount: number
  pendingCount: number
  loading: boolean
  creating: boolean
  error: string | null
  onCreate: () => void
  onOpen: () => void
}) {
  const { t } = useI18n()
  const [showFullDescription, setShowFullDescription] = useState(false)

  return (
    <section className="flex min-h-0 flex-1 items-center justify-center py-8">
      <FadeContent duration={0.55} y={16} blur={false} className="w-full">
        <section className="relative mb-8 w-full sm:mb-10">
          <div className="relative overflow-hidden rounded-3xl border border-black/10 dark:border-white/10">
            <div
              aria-hidden
              className={cn(
                'absolute inset-0',
                'bg-[linear-gradient(110deg,rgba(122,30,30,0.42),rgba(122,30,30,0.18),rgba(122,30,30,0.34))]',
                'dark:bg-[linear-gradient(110deg,rgba(122,30,30,0.38),rgba(122,30,30,0.14),rgba(122,30,30,0.28))]'
              )}
            />
            <div
              aria-hidden
              className={cn(
                'absolute -inset-24 opacity-25 blur-2xl',
                'bg-[conic-gradient(from_180deg_at_50%_50%,rgba(255,122,122,0.55),rgba(255,205,122,0.40),rgba(122,255,227,0.40),rgba(163,122,255,0.40),rgba(255,122,122,0.55))]',
                'motion-safe:animate-[spin_24s_linear_infinite]',
                'md:opacity-25',
                'max-md:opacity-20'
              )}
            />
            <Noise size={220} className="opacity-[0.06] mix-blend-overlay sm:opacity-[0.07]" />

            <div className="relative px-4 py-5 sm:px-8 sm:py-8 lg:px-10 lg:py-10">
              <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
                <div className="min-w-0">
                  <div className="inline-flex items-center gap-2 rounded-full border border-black/10 bg-white/45 px-3 py-1.5 text-xs font-medium text-foreground/80 backdrop-blur-sm dark:border-black/10 dark:bg-white/55 dark:text-foreground/80">
                    <Sparkles className="h-3.5 w-3.5" />
                    {t('landingEyebrow')}
                  </div>

                  <h1 className="mt-4 text-3xl font-semibold tracking-tight text-foreground sm:text-5xl">
                    {t('heroTitle')}
                  </h1>

                  <p
                    className={cn(
                      'mt-2 max-w-2xl text-sm leading-relaxed text-foreground/75 sm:text-base',
                      !showFullDescription && 'line-clamp-2 sm:line-clamp-none'
                    )}
                  >
                    {t('heroBody')}
                  </p>

                  <button
                    type="button"
                    className="mt-1 text-[11px] font-medium text-foreground/70 underline underline-offset-4 sm:hidden"
                    onClick={() => setShowFullDescription((value) => !value)}
                  >
                    {showFullDescription ? 'Show less' : 'Read more'}
                  </button>

                  <div className="mt-4">
                    <SpotlightCard
                      className={cn(
                        'flex w-full items-center gap-2 overflow-x-auto rounded-2xl px-3 py-2 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden',
                        'sm:inline-flex sm:w-auto sm:flex-wrap',
                        'border border-black/10 bg-white/40 backdrop-blur-sm',
                        'dark:border-black/10 dark:bg-white/55'
                      )}
                      spotlightColor="rgba(255,255,255,0.22)"
                    >
                      <StatBadge loading={loading} label={t('landingQuestCount')} value={projectCount} />
                      <StatBadge loading={loading} label={t('pending')} value={pendingCount} />
                    </SpotlightCard>
                  </div>
                </div>

                <div className="w-full sm:w-auto">
                  <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:justify-end">
                    <GlareHover className="w-full rounded-full sm:w-auto">
                      <Button
                        size="lg"
                        onClick={onCreate}
                        disabled={creating}
                        className="h-11 w-full gap-2 rounded-full px-6 sm:w-auto"
                      >
                        <Plus className="h-4 w-4" />
                        {creating ? t('loading') : t('createProject')}
                      </Button>
                    </GlareHover>

                    <GlareHover className="w-full rounded-full sm:w-auto">
                      <Button
                        variant="outline"
                        size="lg"
                        onClick={onOpen}
                        className="h-11 w-full gap-2 rounded-full border-black/10 bg-white/50 px-6 hover:bg-white/60 dark:border-black/10 dark:bg-white/58 dark:hover:bg-white/68 sm:w-auto"
                      >
                        <FolderOpen className="h-4 w-4" />
                        {t('openProject')}
                      </Button>
                    </GlareHover>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {error ? (
            <div className="mt-4 rounded-2xl border border-amber-500/30 bg-amber-50/70 px-4 py-3 text-sm text-amber-900 dark:border-amber-400/20 dark:bg-amber-500/10 dark:text-amber-100">
              {error}
            </div>
          ) : null}
        </section>
      </FadeContent>
    </section>
  )
}

function StatBadge({
  loading,
  label,
  value,
}: {
  loading: boolean
  label: string
  value: number
}) {
  return (
    <Badge
      variant="secondary"
      className="shrink-0 border border-black/10 bg-white/60 text-foreground dark:border-black/10 dark:bg-white/62 dark:text-foreground"
    >
      {loading ? (
        <span className="tabular-nums">...</span>
      ) : (
        <CountUp to={value} duration={0.9} className="tabular-nums" />
      )}{' '}
      {label}
    </Badge>
  )
}
