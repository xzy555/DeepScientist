export const WORKSPACE_LEFT_VISIBILITY_EVENT = 'ds:workspace:left-visibility'
export const QUEST_WORKSPACE_VIEW_EVENT = 'ds:quest:workspace-view'

export type WorkspaceLeftVisibilityDetail = {
  projectId: string
  visible: boolean
}

export type QuestWorkspaceView = 'canvas' | 'details' | 'terminal'

export type QuestWorkspaceViewDetail = {
  projectId: string
  view: QuestWorkspaceView
}

export function dispatchWorkspaceLeftVisibility(detail: WorkspaceLeftVisibilityDetail) {
  if (typeof window === 'undefined') return
  window.dispatchEvent(
    new CustomEvent<WorkspaceLeftVisibilityDetail>(WORKSPACE_LEFT_VISIBILITY_EVENT, {
      detail,
    })
  )
}

export function dispatchQuestWorkspaceView(detail: QuestWorkspaceViewDetail) {
  if (typeof window === 'undefined') return
  window.dispatchEvent(
    new CustomEvent<QuestWorkspaceViewDetail>(QUEST_WORKSPACE_VIEW_EVENT, {
      detail,
    })
  )
}
