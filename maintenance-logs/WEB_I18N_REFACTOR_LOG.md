# Web I18n Refactor Log

This file records the ongoing work to make the Web UI render as one consistent language per locale.

## Goal

- English mode: the visible UI should render in English only.
- Chinese mode: the visible UI should render in Chinese only.
- Keep necessary original English terms where they are product names, protocol names, file formats, connector names, or other stable technical identifiers.

## Scope

Primary surfaces included in this refactor:

1. landing / onboarding
2. Start Research dialog
3. workspace
4. settings / connector setup

## Progress

- Created refactor log files in both English and Chinese.
- Audited the current i18n architecture and confirmed the UI currently mixes:
  - a legacy top-level i18n provider
  - a newer namespaced message-hook system
  - component-local bilingual copy blocks
  - hardcoded English / Chinese strings
- Normalized the landing content source so the hero copy, stages, feature cards, and terminal narrative now resolve from locale-specific single-language bundles.
- Removed intentional bilingual rendering from the landing entry coach dialog.
- Updated landing and projects language-toggle buttons so the button label itself no longer injects the opposite language as raw UI text.
- Wired the workspace terminal pane and quest settings connector pane into the `workspace` i18n namespace for their most visible hardcoded labels.
- Added a reusable Chinese UI copy normalizer and applied it to:
  - `CreateProjectDialog`
  - `ConnectorSettingsForm`
- Verified the web bundle still builds successfully with `npm --prefix src/ui run build`.
- Fixed the Settings left navigation item so `Baselines` now renders as `基线` in Chinese mode.

## Files Updated In This Round

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

## Notes

- This log will be updated as files are changed during the refactor.
