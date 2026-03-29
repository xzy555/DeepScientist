# Web 界面国际化整理记录

这份文件用于记录本轮 Web 前端语言统一改造过程，目标是让不同语言模式下的页面展示保持单语且规整。

## 目标

- 英文模式：界面可见内容尽量统一为英文。
- 中文模式：界面可见内容尽量统一为中文。
- 对于必要的英文原文，例如产品名、协议名、文件格式、连接器名或稳定技术标识，可以保留英文。

## 范围

本轮改造优先覆盖：

1. 首页 / onboarding
2. Start Research 弹窗
3. Workspace 工作区
4. Settings / Connector 配置

## 当前进度

- 已在根目录创建中英文两份改造记录文件。
- 已完成现状排查，确认当前前端同时混用了：
  - 老的顶层 i18n provider
  - 新的 namespaced message hook
  - 组件内部自带的双语 copy
  - 直接写死的中英文字符串
- 已将首页 Hero 主内容源整理为按 locale 输出的单语 bundle，覆盖 hero 文案、阶段卡片、功能卡片和终端叙事。
- 已移除首页入口引导弹窗中故意并排展示的中英双语块。
- 已更新首页和项目页的语言切换按钮，避免按钮本身再把另一种语言直接暴露成界面主文本。
- 已将工作区终端面板和项目设置中的连接器绑定面板，接入 `workspace` 命名空间翻译。
- 已新增一个中文 UI 术语规整 helper，并应用到：
  - `CreateProjectDialog`
  - `ConnectorSettingsForm`
- 已使用 `npm --prefix src/ui run build` 验证前端仍能成功构建。
- 已修复 Settings 左侧导航中的 `Baselines` 文案，中文模式下现在显示为 `基线`。

## 本轮已修改文件

- `src/ui/src/components/landing/hero-content.ts`
- `src/ui/src/components/landing/Hero.tsx`
- `src/ui/src/components/landing/HeroNav.tsx`
- `src/ui/src/components/landing/HeroProgress.tsx`
- `src/ui/src/components/landing/HeroSections.tsx`
- `src/ui/src/components/landing/HeroTerminal.tsx`
- `src/ui/src/components/landing/EntryCoachDialog.tsx`
- `src/ui/src/components/projects/ProjectsAppBar.tsx`
- `src/ui/src/components/projects/CreateProjectDialog.tsx`
- `src/ui/src/components/workspace/QuestWorkspaceSurface.tsx`
- `src/ui/src/components/workspace/QuestSettingsSurface.tsx`
- `src/ui/src/components/settings/ConnectorSettingsForm.tsx`
- `src/ui/src/lib/i18n/messages/workspace.ts`
- `src/ui/src/lib/i18n/normalizeZhUiCopy.ts`

## 说明

- 后续每次改动过的文件和结果都会继续写入这份日志。
