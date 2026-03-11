import { BookmarkPlus, CircleHelp, Lock, RotateCcw, Sparkles } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'

import { OverlayDialog } from '@/components/home/OverlayDialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useI18n } from '@/lib/i18n'
import {
  applyStartResearchContractPreset,
  compileStartResearchPrompt,
  defaultStartResearchTemplate,
  detectStartResearchContractPreset,
  deriveQuestRepoId,
  listStartResearchContractPresets,
  loadStartResearchHistory,
  loadStartResearchTemplate,
  saveStartResearchDraft,
  saveStartResearchTemplate,
  slugifyQuestRepo,
  type StartResearchContractPresetId,
  type StartResearchTemplate,
  type StartResearchTemplateEntry,
} from '@/lib/startResearch'
import { cn } from '@/lib/utils'

const copy = {
  en: {
    title: 'Start Research',
    body: 'Configure kickoff context on the left, or edit the final PI prompt directly on the right.',
    formTitle: 'Context Form',
    formHint: 'Each field adds concrete context for the first research round.',
    preview: 'Prompt preview',
    previewBody: 'This is the exact kickoff content that will be written into the new quest.',
    manual: 'Manual edit active',
    manualTitle: 'Preview edited manually: form is now locked.',
    manualBody: 'Use “Restore form editing” to regenerate the prompt from the left form and unlock inputs.',
    restore: 'Restore form editing',
    template: 'Saved startup template',
    templateHint: 'Reuse a previous startup template when the same research shape appears again.',
    noTemplates: 'No saved templates yet',
    useTemplate: 'Use template',
    questTarget: 'Quest target',
    targetHint: 'This launch creates a new quest repository and seeds the first PI-facing request.',
    targetMode: 'Quest repository',
    targetModeValue: 'Create new quest',
    targetRunner: 'Runner',
    targetRunnerValue: 'Codex / local daemon',
    basics: 'Core research brief',
    references: 'Baseline & references',
    policy: 'Research contract',
    contractProfiles: 'Launch profiles',
    contractProfilesHint: 'Apply one built-in research contract, then fine-tune the fields below if needed.',
    contractProfilesCustomHint: 'Current selections no longer match a built-in launch profile.',
    objectives: 'Goals',
    titleLabel: 'Quest title',
    titlePlaceholder: 'A short human-readable research title',
    titleHelp: 'This is the display title shown in the workspace and quest cards.',
    repoLabel: 'Quest repository id',
    repoPlaceholder: 'auto-generated-from-title',
    repoHelp: 'This becomes the quest folder name under `~/DeepScientist/quests/`. You can customize it.',
    goalLabel: 'Primary research request',
    goalPlaceholder: 'State the core scientific question, target paper, hypothesis, and what success would look like.',
    goalHelp: 'This should describe the actual problem to solve, not implementation details.',
    baselineRoot: 'Baseline root id',
    baselineRootPlaceholder: 'baseline/<name> or artifact id',
    baselineRootHelp: 'Use this when you already have a reusable baseline stored in DeepScientist.',
    baselineUrls: 'Baseline links',
    baselineUrlsPlaceholder: 'One repository or artifact URL per line',
    baselineUrlsHelp: 'Provide source repositories or artifacts that help recover the baseline quickly.',
    paperUrls: 'Reference papers / repos',
    paperUrlsPlaceholder: 'Relevant papers, code, benchmarks, or leaderboards',
    paperUrlsHelp: 'These references help the agent scope the problem and compare against prior work.',
    runtimeConstraintsLabel: 'Runtime constraints',
    runtimeConstraintsPlaceholder: 'Budget, hardware, privacy, storage, data access, or deadline constraints',
    runtimeConstraintsHelp: 'Anything here becomes a hard operating rule for the first research round.',
    objectivesLabel: 'Goals',
    objectivesPlaceholder: 'Describe what this quest should achieve in the first meaningful research cycle.',
    objectivesHelp: 'Use short bullet-like lines such as establish baseline, choose direction, or produce an analysis-ready result.',
    scopeLabel: 'Scope',
    scopeHelp: 'Choose how far the first research round is allowed to go.',
    baselineModeLabel: 'Baseline policy',
    baselineModeHelp: 'Choose what to do when the baseline is missing, partial, or difficult to restore.',
    resourcePolicyLabel: 'Resource policy',
    resourcePolicyHelp: 'This controls how cautious or aggressive the first round should be with compute and time.',
    timeBudgetLabel: 'Time budget per round (hours)',
    timeBudgetHelp: 'This is the expected time budget for one research round, not the whole life of the quest.',
    gitStrategyLabel: 'Git strategy',
    gitStrategyHelp: 'This tells the agent how boldly to branch and how carefully to integrate results.',
    languageLabel: 'User language',
    languageHelp: 'The kickoff prompt and later communication should prefer this language by default.',
    promptRequired: 'Prompt preview cannot be empty.',
    goalRequired: 'Please provide a research request, or edit the preview manually.',
    footer: 'Create quest immediately after review.',
    create: 'Create quest',
    cancel: 'Cancel',
    scopeOptions: {
      baseline_only: 'Baseline only — stop after a strong reusable baseline is established.',
      baseline_plus_direction: 'Baseline + direction — secure baseline and test one justified direction.',
      full_research: 'Full research — baseline, choice of direction, implementation, and analysis readiness.',
    },
    baselineModeOptions: {
      existing: 'Use existing baseline — trust the stored baseline first, then verify it.',
      restore_from_url: 'Restore from URL — rebuild the baseline from source repositories or artifacts.',
      allow_degraded_minimal_reproduction: 'Allow degraded reproduction — accept a weaker but measurable fallback when exact recovery fails.',
      stop_if_insufficient: 'Stop if insufficient — pause instead of pretending the baseline is valid.',
    },
    resourcePolicyOptions: {
      conservative: 'Conservative — keep the first round small, cheap, and low risk.',
      balanced: 'Balanced — move steadily while still controlling cost and uncertainty.',
      aggressive: 'Aggressive — spend more resources to search faster and broader.',
    },
    gitStrategyOptions: {
      branch_per_analysis_then_paper: 'Branch per analysis then paper — split main and analysis work before final integration.',
      semantic_head_plus_controlled_integration: 'Semantic head + controlled integration — keep a cleaner main line and merge more selectively.',
      manual_integration_only: 'Manual integration only — avoid automatic integration and require explicit merge decisions.',
    },
    contractPresetOptions: {
      safe_baseline: {
        title: 'Safe baseline audit',
        meta: 'Baseline only · Conservative · 8h',
        body: 'Protect the first round. If the baseline is weak or missing, stop and report instead of forcing the route.',
      },
      direction_probe: {
        title: 'Balanced direction probe',
        meta: 'Baseline + direction · Balanced · 24h',
        body: 'Build a trustworthy baseline, then test one justified direction without overcommitting resources.',
      },
      full_sprint: {
        title: 'Full research sprint',
        meta: 'Full research · Aggressive · 48h',
        body: 'Use a larger first round to reach baseline, implementation, and analysis-ready evidence faster.',
      },
    },
  },
  zh: {
    title: 'Start Research',
    body: '左侧配置研究启动上下文，右侧是最终发给 PI 的完整 kickoff prompt。',
    formTitle: '上下文表单',
    formHint: '每一项都在为第一轮研究提供清晰、可执行的上下文。',
    preview: 'Prompt 预览',
    previewBody: '这里展示的是即将写入新 quest 的完整启动内容。',
    manual: '手工编辑已启用',
    manualTitle: '你已手工修改预览，左侧表单暂时锁定。',
    manualBody: '点击“恢复表单驱动”后，会重新根据左侧表单生成 prompt，并解除锁定。',
    restore: '恢复表单驱动',
    template: '已保存的启动模板',
    templateHint: '当研究形态相近时，可以快速复用过去的启动模板。',
    noTemplates: '还没有已保存模板',
    useTemplate: '使用模板',
    questTarget: 'Quest 目标',
    targetHint: '当前启动会创建一个新的 quest 仓库，并写入第一条面向 PI 的启动请求。',
    targetMode: 'Quest 仓库',
    targetModeValue: '创建新 quest',
    targetRunner: 'Runner',
    targetRunnerValue: 'Codex / 本地 daemon',
    basics: '核心研究简述',
    references: 'Baseline 与参考',
    policy: '研究合同',
    contractProfiles: '启动配置',
    contractProfilesHint: '先套用一个内置研究合同，再按需微调下面的字段。',
    contractProfilesCustomHint: '当前选择已经偏离内置启动配置，属于自定义合同。',
    objectives: '目标',
    titleLabel: '课题标题',
    titlePlaceholder: '一个简洁易读的研究标题',
    titleHelp: '这是工作区和 quest 卡片中展示给用户看的标题。',
    repoLabel: 'Quest 仓库 id',
    repoPlaceholder: '默认会根据标题自动生成',
    repoHelp: '这会成为 `~/DeepScientist/quests/` 下的 quest 文件夹名，你可以手动定制。',
    goalLabel: '核心研究请求',
    goalPlaceholder: '清楚说明科学问题、目标论文、核心假设，以及什么结果算成功。',
    goalHelp: '这里应该描述真正要解决的问题，而不是过早写实现细节。',
    baselineRoot: 'Baseline root id',
    baselineRootPlaceholder: 'baseline/<name> 或 artifact id',
    baselineRootHelp: '如果你已经在 DeepScientist 里有可复用 baseline，可以在这里直接指定。',
    baselineUrls: 'Baseline 链接',
    baselineUrlsPlaceholder: '每行一个仓库或 artifact 链接',
    baselineUrlsHelp: '这些链接用于帮助系统更快恢复或修复 baseline。',
    paperUrls: '参考论文 / 仓库',
    paperUrlsPlaceholder: '相关论文、代码、benchmark 或 leaderboard',
    paperUrlsHelp: '这些参考资料会帮助 agent 更好地界定问题和比较工作。',
    runtimeConstraintsLabel: '运行约束',
    runtimeConstraintsPlaceholder: '预算、硬件、隐私、存储、数据访问、截止时间等限制',
    runtimeConstraintsHelp: '写在这里的内容会被视为第一轮研究中的硬性运行约束。',
    objectivesLabel: '目标',
    objectivesPlaceholder: '描述这一轮研究需要达成什么，例如建立 baseline、筛选方向、得到可分析结果等。',
    objectivesHelp: '建议按短句逐行写明，例如“建立可信 baseline”“判断是否值得实现某方向”。',
    scopeLabel: '研究范围',
    scopeHelp: '决定第一轮研究最多允许推进到什么程度。',
    baselineModeLabel: 'Baseline 策略',
    baselineModeHelp: '决定 baseline 缺失、不完整或很难恢复时应该如何处理。',
    resourcePolicyLabel: '资源策略',
    resourcePolicyHelp: '决定第一轮研究在算力、时间与风险上是保守还是激进。',
    timeBudgetLabel: '每一轮研究的时间预算（小时）',
    timeBudgetHelp: '这里指的是一次研究 round 的预期耗时，不是整个 quest 生命周期的总耗时。',
    gitStrategyLabel: 'Git 策略',
    gitStrategyHelp: '告诉 agent 应该多积极地分支，以及多谨慎地做结果集成。',
    languageLabel: '用户语言',
    languageHelp: '默认希望 kickoff prompt 与后续交流优先使用的语言。',
    promptRequired: 'Prompt 预览不能为空。',
    goalRequired: '请填写研究请求，或直接在右侧手工编辑 prompt。',
    footer: '确认后会立即创建 quest。',
    create: '创建 quest',
    cancel: '取消',
    scopeOptions: {
      baseline_only: '仅 baseline —— 建立一个可信且可复用的 baseline 后即停止本轮。',
      baseline_plus_direction: 'baseline + 方向 —— 先建立 baseline，再验证一个有依据的改进方向。',
      full_research: '完整研究 —— baseline、方向选择、实现推进，以及进入分析准备阶段。',
    },
    baselineModeOptions: {
      existing: '使用现有 baseline —— 优先复用已存储的 baseline，并先验证其可信度。',
      restore_from_url: '从链接恢复 —— 根据仓库或 artifact 链接恢复 baseline。',
      allow_degraded_minimal_reproduction: '允许降级复现 —— 精确恢复失败时，可接受较弱但可测的替代 baseline。',
      stop_if_insufficient: '证据不足则停止 —— 宁可暂停，也不伪造一个不可信的 baseline。',
    },
    resourcePolicyOptions: {
      conservative: '保守 —— 第一轮尽量小步、低成本、低风险。',
      balanced: '平衡 —— 稳步推进，同时控制成本与不确定性。',
      aggressive: '激进 —— 愿意投入更多资源来更快、更广地探索。',
    },
    gitStrategyOptions: {
      branch_per_analysis_then_paper: '主实验 / 分析分支拆分 —— 先拆开主实验与分析实验，再统一汇总写作。',
      semantic_head_plus_controlled_integration: '语义主线 + 受控集成 —— 保持更干净的主线，只合并经过控制的结果。',
      manual_integration_only: '仅手动集成 —— 避免自动集成，所有合并都需要显式决策。',
    },
    contractPresetOptions: {
      safe_baseline: {
        title: '安全基线审计',
        meta: '仅 baseline · 保守 · 8 小时',
        body: '把第一轮收紧。如果 baseline 证据不足，就停止并汇报，而不是强行推进。',
      },
      direction_probe: {
        title: '平衡方向试探',
        meta: 'baseline + 方向 · 平衡 · 24 小时',
        body: '先建立可信 baseline，再在受控预算内验证一个有依据的改进方向。',
      },
      full_sprint: {
        title: '完整研究冲刺',
        meta: '完整研究 · 激进 · 48 小时',
        body: '用更大的首轮预算，尽快推进到 baseline、实现与分析准备就绪。',
      },
    },
  },
} as const

const selectClassName =
  'h-9 rounded-[10px] border border-[rgba(45,42,38,0.1)] bg-white/78 px-3 text-xs text-[rgba(38,36,33,0.95)] outline-none transition focus:border-[rgba(45,42,38,0.18)] dark:border-[rgba(45,42,38,0.1)] dark:bg-white/82 dark:text-[rgba(38,36,33,0.95)] dark:focus:border-[rgba(45,42,38,0.18)]'

const panelClass =
  'rounded-xl border border-[rgba(45,42,38,0.09)] bg-[rgba(255,255,255,0.76)] shadow-[0_12px_30px_-24px_rgba(45,42,38,0.32)] backdrop-blur-xl dark:border-[rgba(45,42,38,0.09)] dark:bg-[rgba(255,255,255,0.82)]'

function FieldHelp({
  text,
}: {
  text: string
}) {
  return (
    <div className="group relative inline-flex">
      <button
        type="button"
        tabIndex={-1}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[rgba(107,103,97,0.78)] transition hover:text-[rgba(45,42,38,0.95)] dark:text-[rgba(107,103,97,0.78)] dark:hover:text-[rgba(45,42,38,0.95)]"
        aria-label={text}
      >
        <CircleHelp className="h-3.5 w-3.5" />
      </button>
      <div className="pointer-events-none absolute left-1/2 top-[calc(100%+0.45rem)] z-20 hidden w-64 -translate-x-1/2 rounded-[14px] border border-[rgba(45,42,38,0.1)] bg-[rgba(255,255,255,0.97)] px-3 py-2 text-[11px] leading-5 text-[rgba(56,52,47,0.92)] shadow-[0_20px_40px_-28px_rgba(45,42,38,0.45)] group-hover:block dark:border-[rgba(45,42,38,0.1)] dark:bg-[rgba(255,255,255,0.97)] dark:text-[rgba(56,52,47,0.92)]">
        {text}
      </div>
    </div>
  )
}

function InlineField({
  label,
  help,
  hint,
  children,
}: {
  label: string
  help?: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-[rgba(75,73,69,0.78)] dark:text-[rgba(75,73,69,0.78)]">
        <span>{label}</span>
        {help ? <FieldHelp text={help} /> : null}
      </div>
      {hint ? <div className="text-[11px] leading-5 text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">{hint}</div> : null}
      {children}
    </div>
  )
}

type ChoiceItem<T extends string> = {
  value: T
  title: string
  description: string
  meta?: string
}

function ChoiceField<T extends string>({
  label,
  help,
  hint,
  value,
  items,
  onChange,
  disabled = false,
}: {
  label: string
  help?: string
  hint?: string
  value: T | null
  items: ChoiceItem<T>[]
  onChange: (value: T) => void
  disabled?: boolean
}) {
  return (
    <InlineField label={label} help={help} hint={hint}>
      <div role="radiogroup" aria-label={label} className="space-y-2">
        {items.map((item) => {
          const active = item.value === value
          return (
            <button
              key={item.value}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={disabled}
              onClick={() => onChange(item.value)}
              className={cn(
                'flex w-full items-start gap-3 rounded-[14px] border px-3 py-3 text-left transition',
                'disabled:cursor-not-allowed disabled:opacity-60',
                active
                  ? 'border-[rgba(126,77,42,0.32)] bg-[rgba(126,77,42,0.08)] shadow-[0_14px_26px_-22px_rgba(90,56,35,0.55)] dark:border-[rgba(126,77,42,0.32)] dark:bg-[rgba(126,77,42,0.08)]'
                  : 'border-[rgba(45,42,38,0.08)] bg-white/60 hover:border-[rgba(45,42,38,0.14)] hover:bg-white/82 dark:border-[rgba(45,42,38,0.08)] dark:bg-white/70 dark:hover:border-[rgba(45,42,38,0.14)] dark:hover:bg-white/86'
              )}
            >
              <span
                aria-hidden
                className={cn(
                  'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition',
                  active
                    ? 'border-[rgba(126,77,42,0.78)] bg-[rgba(126,77,42,0.14)] dark:border-[rgba(126,77,42,0.78)] dark:bg-[rgba(126,77,42,0.14)]'
                    : 'border-[rgba(107,103,97,0.34)] bg-transparent dark:border-[rgba(107,103,97,0.34)]'
                )}
              >
                <span
                  className={cn(
                    'h-1.5 w-1.5 rounded-full transition',
                    active ? 'bg-[rgba(126,77,42,0.92)] dark:bg-[rgba(126,77,42,0.92)]' : 'bg-transparent'
                  )}
                />
              </span>
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-xs font-semibold text-[rgba(38,36,33,0.95)] dark:text-[rgba(38,36,33,0.95)]">
                    {item.title}
                  </span>
                  {item.meta ? (
                    <span className="text-[10px] uppercase tracking-[0.16em] text-[rgba(107,103,97,0.78)] dark:text-[rgba(107,103,97,0.78)]">
                      {item.meta}
                    </span>
                  ) : null}
                </span>
                <span className="mt-1 block text-[11px] leading-5 text-[rgba(86,82,77,0.82)] dark:text-[rgba(86,82,77,0.82)]">
                  {item.description}
                </span>
              </span>
            </button>
          )
        })}
      </div>
    </InlineField>
  )
}

function SectionCard({
  title,
  children,
  muted = false,
}: {
  title: string
  children: ReactNode
  muted?: boolean
}) {
  return (
    <div
      className={cn(
        'rounded-xl border p-3',
        muted
          ? 'border-[rgba(45,42,38,0.08)] bg-[rgba(244,239,233,0.62)] dark:border-[rgba(45,42,38,0.08)] dark:bg-[rgba(244,239,233,0.72)]'
          : panelClass
      )}
    >
      <div className="text-sm font-semibold text-[rgba(38,36,33,0.95)] dark:text-[rgba(38,36,33,0.95)]">{title}</div>
      <div className="mt-3 space-y-3">{children}</div>
    </div>
  )
}

function compactTemplateLabel(item: StartResearchTemplateEntry, locale: 'en' | 'zh') {
  const goal = item.goal || (locale === 'zh' ? '未命名模板' : 'Untitled template')
  const title = item.title ? `${item.title} · ` : ''
  return `${title}${goal}`.slice(0, 72)
}

function splitOptionCopy(text: string) {
  const [title, ...rest] = text.split(/\s+[—-]{1,2}\s+/)
  return {
    title: title.trim(),
    description: rest.join(' — ').trim() || title.trim(),
  }
}

export function CreateProjectDialog({
  open,
  loading,
  error,
  initialGoal = '',
  onClose,
  onCreate,
}: {
  open: boolean
  loading?: boolean
  error?: string | null
  initialGoal?: string
  onClose: () => void
  onCreate: (payload: { title: string; goal: string; quest_id?: string }) => Promise<void>
}) {
  const { locale } = useI18n()
  const t = copy[locale]
  const [form, setForm] = useState<StartResearchTemplate>(defaultStartResearchTemplate(locale))
  const [promptDraft, setPromptDraft] = useState('')
  const [manualOverride, setManualOverride] = useState(false)
  const [templates, setTemplates] = useState<StartResearchTemplateEntry[]>([])
  const [selectedTemplateId, setSelectedTemplateId] = useState('__latest__')

  const activeContractPresetId = useMemo(
    () => detectStartResearchContractPreset(form),
    [form.baseline_mode, form.git_strategy, form.resource_policy, form.scope, form.time_budget_hours]
  )

  const contractPresetItems = useMemo(
    () =>
      listStartResearchContractPresets().map((preset) => ({
        value: preset.id,
        title: t.contractPresetOptions[preset.id].title,
        meta: t.contractPresetOptions[preset.id].meta,
        description: t.contractPresetOptions[preset.id].body,
      })),
    [t]
  )

  const scopeItems = useMemo(
    () =>
      ([
        ['baseline_only', t.scopeOptions.baseline_only],
        ['baseline_plus_direction', t.scopeOptions.baseline_plus_direction],
        ['full_research', t.scopeOptions.full_research],
      ] as const).map(([value, text]) => ({
        value,
        ...splitOptionCopy(text),
      })),
    [t]
  )

  const baselineModeItems = useMemo(
    () =>
      ([
        ['existing', t.baselineModeOptions.existing],
        ['restore_from_url', t.baselineModeOptions.restore_from_url],
        ['allow_degraded_minimal_reproduction', t.baselineModeOptions.allow_degraded_minimal_reproduction],
        ['stop_if_insufficient', t.baselineModeOptions.stop_if_insufficient],
      ] as const).map(([value, text]) => ({
        value,
        ...splitOptionCopy(text),
      })),
    [t]
  )

  const resourcePolicyItems = useMemo(
    () =>
      ([
        ['conservative', t.resourcePolicyOptions.conservative],
        ['balanced', t.resourcePolicyOptions.balanced],
        ['aggressive', t.resourcePolicyOptions.aggressive],
      ] as const).map(([value, text]) => ({
        value,
        ...splitOptionCopy(text),
      })),
    [t]
  )

  const gitStrategyItems = useMemo(
    () =>
      ([
        ['branch_per_analysis_then_paper', t.gitStrategyOptions.branch_per_analysis_then_paper],
        ['semantic_head_plus_controlled_integration', t.gitStrategyOptions.semantic_head_plus_controlled_integration],
        ['manual_integration_only', t.gitStrategyOptions.manual_integration_only],
      ] as const).map(([value, text]) => ({
        value,
        ...splitOptionCopy(text),
      })),
    [t]
  )

  useEffect(() => {
    if (!open) {
      return
    }
    const next = loadStartResearchTemplate(locale)
    const withSeed = {
      ...next,
      goal: initialGoal || next.goal,
      user_language: locale,
    }
    setForm({
      ...withSeed,
      quest_id: withSeed.quest_id || deriveQuestRepoId(withSeed),
    })
    setTemplates(loadStartResearchHistory())
    setSelectedTemplateId('__latest__')
    setManualOverride(false)
  }, [initialGoal, locale, open])

  const compiledPromptPreview = useMemo(() => compileStartResearchPrompt(form), [form])

  useEffect(() => {
    if (!open || manualOverride) {
      return
    }
    setPromptDraft(compiledPromptPreview)
  }, [compiledPromptPreview, manualOverride, open])

  const finalPrompt = promptDraft.trim() || (!manualOverride ? compiledPromptPreview.trim() : '')
  const promptRequired = open && !finalPrompt
  const goalRequired = open && !manualOverride && !form.goal.trim()

  const setField = <K extends keyof StartResearchTemplate>(key: K, value: StartResearchTemplate[K]) => {
    setForm((current) => {
      const next = { ...current, [key]: value }
      if (key === 'title' && !current.quest_id) {
        next.quest_id = deriveQuestRepoId({
          title: String(value),
          goal: current.goal,
        })
      }
      if (key === 'goal' && !current.quest_id) {
        next.quest_id = deriveQuestRepoId({
          title: current.title,
          goal: String(value),
        })
      }
      saveStartResearchDraft(next)
      return next
    })
  }

  const handlePromptChange = (value: string) => {
    if (!manualOverride && value !== compiledPromptPreview) {
      setManualOverride(true)
    }
    setPromptDraft(value)
  }

  const handleRestore = () => {
    setManualOverride(false)
    setPromptDraft(compiledPromptPreview)
  }

  const handleTemplateChange = (templateId: string) => {
    setSelectedTemplateId(templateId)
    if (templateId === '__latest__') {
      const latest = loadStartResearchTemplate(locale)
      setManualOverride(false)
      setForm({
        ...latest,
        goal: initialGoal || latest.goal,
        user_language: locale,
        quest_id: latest.quest_id || deriveQuestRepoId(latest),
      })
      return
    }
    const next = templates.find((item) => item.id === templateId)
    if (!next) {
      return
    }
    setManualOverride(false)
    setForm({
      title: next.title,
      quest_id: next.quest_id || deriveQuestRepoId(next),
      goal: next.goal,
      baseline_root_id: next.baseline_root_id,
      baseline_urls: next.baseline_urls,
      paper_urls: next.paper_urls,
      runtime_constraints: next.runtime_constraints,
      objectives: next.objectives,
      scope: next.scope,
      baseline_mode: next.baseline_mode,
      resource_policy: next.resource_policy,
      time_budget_hours: next.time_budget_hours,
      git_strategy: next.git_strategy,
      user_language: locale,
    })
  }

  const applyContractPreset = (presetId: StartResearchContractPresetId) => {
    setForm((current) => {
      const next = applyStartResearchContractPreset(current, presetId)
      saveStartResearchDraft(next)
      return next
    })
  }

  const handleCreate = async () => {
    if (!manualOverride && !form.goal.trim()) {
      return
    }
    if (!finalPrompt) {
      return
    }
    const saved = saveStartResearchTemplate(form)
    await onCreate({
      title: saved.title,
      goal: finalPrompt,
      quest_id: saved.quest_id || undefined,
    })
  }

  return (
    <OverlayDialog
      open={open}
      title={t.title}
      description={t.body}
      onClose={onClose}
      className="h-[92vh] max-w-[92vw] rounded-[30px]"
    >
      <div className="grid h-full min-h-0 gap-4 overflow-hidden p-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:p-5">
        <div className={cn(panelClass, 'flex min-h-0 flex-col overflow-hidden')}>
          <div className="shrink-0 border-b border-[rgba(45,42,38,0.08)] px-4 py-4 dark:border-[rgba(45,42,38,0.08)]">
            <div className="text-sm font-semibold text-[rgba(38,36,33,0.95)] dark:text-[rgba(38,36,33,0.95)]">{t.formTitle}</div>
            <div className="mt-1 text-xs text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">{t.formHint}</div>
          </div>

          <div className="feed-scrollbar min-h-0 flex-1 overflow-y-auto overscroll-contain p-4">
            <div className="flex min-h-full flex-col gap-4">
              {manualOverride ? (
                <div className="rounded-lg border border-[#c4a066]/50 bg-[#c4a066]/10 px-3 py-2 text-xs text-[rgba(56,49,35,0.92)]">
                  <div className="flex items-center gap-2 font-semibold">
                    <Lock className="h-3.5 w-3.5" />
                    {t.manualTitle}
                  </div>
                  <div className="mt-1">{t.manualBody}</div>
                </div>
              ) : null}

              <SectionCard title={t.template} muted>
                <InlineField label={t.template} help={t.templateHint} hint={t.templateHint}>
                  <div className="flex gap-2">
                    <select
                      value={selectedTemplateId}
                      onChange={(event) => handleTemplateChange(event.target.value)}
                      className={cn(selectClassName, 'min-w-0 flex-1')}
                      disabled={manualOverride}
                    >
                      <option value="__latest__">{t.useTemplate}: latest draft</option>
                      {templates.length === 0 ? <option value="__empty__">{t.noTemplates}</option> : null}
                      {templates.map((item) => (
                        <option key={item.id} value={item.id}>
                          {compactTemplateLabel(item, locale)}
                        </option>
                      ))}
                    </select>
                    <div className="inline-flex h-9 items-center rounded-[10px] border border-[rgba(45,42,38,0.09)] bg-white/65 px-3 text-[11px] text-[rgba(75,73,69,0.72)] dark:border-[rgba(45,42,38,0.09)] dark:bg-white/72 dark:text-[rgba(75,73,69,0.72)]">
                      {templates.length}
                    </div>
                  </div>
                </InlineField>
              </SectionCard>

              <SectionCard title={t.questTarget} muted>
                <div className="text-[11px] leading-5 text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">{t.targetHint}</div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="rounded-lg border border-[rgba(45,42,38,0.08)] bg-white/70 px-3 py-3 dark:border-[rgba(45,42,38,0.08)] dark:bg-white/76">
                    <div className="text-[11px] text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">{t.targetMode}</div>
                    <div className="mt-1 text-sm font-semibold text-[rgba(38,36,33,0.95)] dark:text-[rgba(38,36,33,0.95)]">{t.targetModeValue}</div>
                  </div>
                  <div className="rounded-lg border border-[rgba(45,42,38,0.08)] bg-white/70 px-3 py-3 dark:border-[rgba(45,42,38,0.08)] dark:bg-white/76">
                    <div className="text-[11px] text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">{t.targetRunner}</div>
                    <div className="mt-1 text-sm font-semibold text-[rgba(38,36,33,0.95)] dark:text-[rgba(38,36,33,0.95)]">{t.targetRunnerValue}</div>
                  </div>
                </div>
              </SectionCard>

              <SectionCard title={t.basics}>
                <InlineField label={t.titleLabel} help={t.titleHelp}>
                  <Input
                    value={form.title}
                    onChange={(event) => setField('title', event.target.value)}
                    placeholder={t.titlePlaceholder}
                    className="rounded-[10px] border-[rgba(45,42,38,0.09)] bg-white/75 text-xs dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78"
                    disabled={manualOverride}
                  />
                </InlineField>

                <InlineField label={t.repoLabel} help={t.repoHelp}>
                  <Input
                    value={form.quest_id}
                    onChange={(event) => setField('quest_id', slugifyQuestRepo(event.target.value))}
                    placeholder={t.repoPlaceholder}
                    className="rounded-[10px] border-[rgba(45,42,38,0.09)] bg-white/75 text-xs dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78"
                    disabled={manualOverride}
                  />
                </InlineField>

                <InlineField label={t.goalLabel} help={t.goalHelp}>
                  <Textarea
                    value={form.goal}
                    onChange={(event) => setField('goal', event.target.value)}
                    placeholder={t.goalPlaceholder}
                    className="min-h-[150px] rounded-[10px] border-[rgba(45,42,38,0.09)] bg-white/75 text-xs leading-5 dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78"
                    disabled={manualOverride}
                  />
                </InlineField>
                {goalRequired ? <div className="text-xs text-[#9a1b1b]">{t.goalRequired}</div> : null}
              </SectionCard>

              <SectionCard title={t.references}>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <InlineField label={t.baselineRoot} help={t.baselineRootHelp}>
                    <Input
                      value={form.baseline_root_id}
                      onChange={(event) => setField('baseline_root_id', event.target.value)}
                      placeholder={t.baselineRootPlaceholder}
                      className="rounded-[10px] border-[rgba(45,42,38,0.09)] bg-white/75 text-xs dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78"
                      disabled={manualOverride}
                    />
                  </InlineField>
                  <InlineField label={t.languageLabel} help={t.languageHelp}>
                    <select
                      value={form.user_language}
                      onChange={(event) => setField('user_language', event.target.value as StartResearchTemplate['user_language'])}
                      className={selectClassName}
                      disabled={manualOverride}
                    >
                      <option value="zh">中文</option>
                      <option value="en">English</option>
                    </select>
                  </InlineField>
                </div>
                <InlineField label={t.baselineUrls} help={t.baselineUrlsHelp}>
                  <Textarea
                    value={form.baseline_urls}
                    onChange={(event) => setField('baseline_urls', event.target.value)}
                    placeholder={t.baselineUrlsPlaceholder}
                    className="min-h-[92px] rounded-[10px] border-[rgba(45,42,38,0.09)] bg-white/75 text-xs leading-5 dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78"
                    disabled={manualOverride}
                  />
                </InlineField>
                <InlineField label={t.paperUrls} help={t.paperUrlsHelp}>
                  <Textarea
                    value={form.paper_urls}
                    onChange={(event) => setField('paper_urls', event.target.value)}
                    placeholder={t.paperUrlsPlaceholder}
                    className="min-h-[92px] rounded-[10px] border-[rgba(45,42,38,0.09)] bg-white/75 text-xs leading-5 dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78"
                    disabled={manualOverride}
                  />
                </InlineField>
              </SectionCard>

              <SectionCard title={t.policy}>
                <ChoiceField
                  label={t.contractProfiles}
                  help={t.contractProfilesHint}
                  hint={t.contractProfilesHint}
                  value={activeContractPresetId}
                  items={contractPresetItems}
                  onChange={applyContractPreset}
                  disabled={manualOverride}
                />
                {!activeContractPresetId ? (
                  <div className="rounded-[14px] border border-[rgba(45,42,38,0.08)] bg-[rgba(244,239,233,0.52)] px-3 py-2 text-[11px] leading-5 text-[rgba(86,82,77,0.82)] dark:border-[rgba(45,42,38,0.08)] dark:bg-[rgba(244,239,233,0.62)] dark:text-[rgba(86,82,77,0.82)]">
                    {t.contractProfilesCustomHint}
                  </div>
                ) : null}
                <ChoiceField
                  label={t.scopeLabel}
                  help={t.scopeHelp}
                  hint={t.scopeHelp}
                  value={form.scope}
                  items={scopeItems}
                  onChange={(value) => setField('scope', value)}
                  disabled={manualOverride}
                />
                <ChoiceField
                  label={t.baselineModeLabel}
                  help={t.baselineModeHelp}
                  hint={t.baselineModeHelp}
                  value={form.baseline_mode}
                  items={baselineModeItems}
                  onChange={(value) => setField('baseline_mode', value)}
                  disabled={manualOverride}
                />
                <ChoiceField
                  label={t.resourcePolicyLabel}
                  help={t.resourcePolicyHelp}
                  hint={t.resourcePolicyHelp}
                  value={form.resource_policy}
                  items={resourcePolicyItems}
                  onChange={(value) => setField('resource_policy', value)}
                  disabled={manualOverride}
                />
                <ChoiceField
                  label={t.gitStrategyLabel}
                  help={t.gitStrategyHelp}
                  hint={t.gitStrategyHelp}
                  value={form.git_strategy}
                  items={gitStrategyItems}
                  onChange={(value) => setField('git_strategy', value)}
                  disabled={manualOverride}
                />
                <InlineField label={t.timeBudgetLabel} help={t.timeBudgetHelp} hint={t.timeBudgetHelp}>
                  <Input
                    value={form.time_budget_hours}
                    onChange={(event) => setField('time_budget_hours', event.target.value)}
                    placeholder="24"
                    className="rounded-[10px] border-[rgba(45,42,38,0.09)] bg-white/75 text-xs dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78"
                    disabled={manualOverride}
                  />
                </InlineField>
                <InlineField label={t.runtimeConstraintsLabel} help={t.runtimeConstraintsHelp}>
                  <Textarea
                    value={form.runtime_constraints}
                    onChange={(event) => setField('runtime_constraints', event.target.value)}
                    placeholder={t.runtimeConstraintsPlaceholder}
                    className="min-h-[92px] rounded-[10px] border-[rgba(45,42,38,0.09)] bg-white/75 text-xs leading-5 dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78"
                    disabled={manualOverride}
                  />
                </InlineField>
              </SectionCard>

              <SectionCard title={t.objectives}>
                <InlineField label={t.objectivesLabel} help={t.objectivesHelp}>
                  <Textarea
                    value={form.objectives}
                    onChange={(event) => setField('objectives', event.target.value)}
                    placeholder={t.objectivesPlaceholder}
                    className="min-h-[120px] rounded-[10px] border-[rgba(45,42,38,0.09)] bg-white/75 text-xs leading-5 dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78"
                    disabled={manualOverride}
                  />
                </InlineField>
              </SectionCard>
            </div>
          </div>
        </div>

        <div className={cn(panelClass, 'flex min-h-0 flex-col overflow-hidden p-4')}>
          <div className="mb-3 flex shrink-0 flex-wrap items-start justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-[rgba(38,36,33,0.95)] dark:text-[rgba(38,36,33,0.95)]">{t.preview}</div>
              <div className="mt-1 text-xs text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">{t.previewBody}</div>
            </div>
            {manualOverride ? (
              <Badge className="rounded-full px-2.5 py-1 text-[10px] uppercase tracking-wide">{t.manual}</Badge>
            ) : null}
          </div>

          <div className="mb-3 shrink-0 grid grid-cols-1 gap-2 sm:grid-cols-3">
            <div className="rounded-lg border border-[rgba(45,42,38,0.09)] bg-[rgba(244,239,233,0.55)] px-3 py-2 text-[11px] dark:border-[rgba(45,42,38,0.09)] dark:bg-[rgba(244,239,233,0.65)]">
              <div className="text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">{t.repoLabel}</div>
              <div className="mt-1 font-semibold text-[rgba(38,36,33,0.95)] dark:text-[rgba(38,36,33,0.95)]">{form.quest_id || deriveQuestRepoId(form) || 'auto-generated'}</div>
            </div>
            <div className="rounded-lg border border-[rgba(45,42,38,0.09)] bg-[rgba(244,239,233,0.55)] px-3 py-2 text-[11px] dark:border-[rgba(45,42,38,0.09)] dark:bg-[rgba(244,239,233,0.65)]">
              <div className="text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">{t.scopeLabel}</div>
              <div className="mt-1 font-semibold text-[rgba(38,36,33,0.95)] dark:text-[rgba(38,36,33,0.95)]">{form.scope.replaceAll('_', ' ')}</div>
            </div>
            <div className="rounded-lg border border-[rgba(45,42,38,0.09)] bg-[rgba(244,239,233,0.55)] px-3 py-2 text-[11px] dark:border-[rgba(45,42,38,0.09)] dark:bg-[rgba(244,239,233,0.65)]">
              <div className="text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">{t.timeBudgetLabel}</div>
              <div className="mt-1 font-semibold text-[rgba(38,36,33,0.95)] dark:text-[rgba(38,36,33,0.95)]">{form.time_budget_hours ? `${form.time_budget_hours}h` : '—'}</div>
            </div>
          </div>

          <textarea
            aria-label={t.preview}
            value={promptDraft}
            onChange={(event) => handlePromptChange(event.target.value)}
            className="feed-scrollbar min-h-0 flex-1 overflow-y-auto overscroll-contain resize-none rounded-xl border border-[rgba(45,42,38,0.09)] bg-white/65 p-3 font-mono text-xs leading-5 text-[rgba(38,36,33,0.95)] outline-none dark:border-[rgba(45,42,38,0.09)] dark:bg-white/78 dark:text-[rgba(38,36,33,0.95)]"
          />

          <div className="mt-2 flex shrink-0 items-center justify-between gap-2 text-[11px] text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">
            <span>{t.footer}</span>
            <span>{promptDraft.length}</span>
          </div>

          {promptRequired ? <div className="mt-2 shrink-0 text-xs text-[#9a1b1b]">{t.promptRequired}</div> : null}
          {error ? <div className="mt-2 shrink-0 text-xs text-[#9a1b1b]">{error}</div> : null}

          <div className="mt-3 flex shrink-0 items-center justify-between gap-3">
            <div className="inline-flex items-center gap-2 text-[11px] text-[rgba(107,103,97,0.72)] dark:text-[rgba(107,103,97,0.72)]">
              <BookmarkPlus className="h-3.5 w-3.5" />
              <span>{templates.length} template(s)</span>
            </div>
            <div className="flex items-center gap-3">
              <Button variant="secondary" disabled={!manualOverride || loading} onClick={handleRestore}>
                <RotateCcw className="h-4 w-4" />
                {t.restore}
              </Button>
              <Button variant="ghost" onClick={onClose}>
                {t.cancel}
              </Button>
              <Button onClick={() => void handleCreate()} disabled={loading || goalRequired || promptRequired}>
                <Sparkles className="h-4 w-4" />
                {loading ? '…' : t.create}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </OverlayDialog>
  )
}
