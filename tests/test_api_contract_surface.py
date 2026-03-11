from __future__ import annotations

from pathlib import Path

from deepscientist.daemon.api.router import match_route


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_backend_routes_cover_shared_web_and_tui_surface() -> None:
    expected_routes = [
        ("GET", "/api/quests", "quests"),
        ("POST", "/api/quests", "quest_create"),
        ("GET", "/api/connectors", "connectors"),
        ("PATCH", "/api/quests/q-001/settings", "quest_settings"),
        ("GET", "/api/quests/q-001/session", "quest_session"),
        ("GET", "/api/quests/q-001/events", "quest_events"),
        ("GET", "/api/quests/q-001/artifacts", "quest_artifacts"),
        ("GET", "/api/quests/q-001/workflow", "workflow"),
        ("GET", "/api/quests/q-001/bash/sessions", "bash_sessions"),
        ("GET", "/api/quests/q-001/bash/sessions/stream", "bash_sessions_stream"),
        ("GET", "/api/quests/q-001/bash/sessions/bash-001", "bash_session"),
        ("GET", "/api/quests/q-001/bash/sessions/bash-001/logs", "bash_logs"),
        ("GET", "/api/quests/q-001/bash/sessions/bash-001/stream", "bash_log_stream"),
        ("POST", "/api/quests/q-001/bash/sessions/bash-001/stop", "bash_stop"),
        ("GET", "/api/quests/q-001/node-traces", "node_traces"),
        ("GET", "/api/quests/q-001/node-traces/stage%3Amain%3Aidea", "node_trace"),
        ("GET", "/api/quests/q-001/graph", "graph"),
        ("GET", "/api/quests/q-001/graph/svg", "graph_asset"),
        ("GET", "/api/quests/q-001/git/branches", "git_branches"),
        ("GET", "/api/quests/q-001/git/log", "git_log"),
        ("GET", "/api/quests/q-001/git/compare", "git_compare"),
        ("GET", "/api/quests/q-001/git/commit", "git_commit"),
        ("GET", "/api/quests/q-001/git/diff-file", "git_diff_file"),
        ("GET", "/api/quests/q-001/git/commit-file", "git_commit_file"),
        ("GET", "/api/quests/q-001/memory", "quest_memory"),
        ("GET", "/api/quests/q-001/documents", "documents"),
        ("GET", "/api/quests/q-001/explorer", "explorer"),
        ("GET", "/api/quests/q-001/documents/asset", "document_asset"),
        ("POST", "/api/quests/q-001/documents/open", "document_open"),
        ("POST", "/api/quests/q-001/documents/assets", "document_asset_upload"),
        ("PUT", "/api/quests/q-001/documents/plan.md", "document_save"),
        ("PUT", "/api/quests/q-001/documents/path::literature/notes.md", "document_save"),
        ("POST", "/api/quests/q-001/chat", "chat"),
        ("POST", "/api/quests/q-001/commands", "command"),
        ("POST", "/api/quests/q-001/control", "quest_control"),
        ("POST", "/api/quests/q-001/runs", "run_create"),
        ("GET", "/api/config/files", "config_files"),
        ("GET", "/api/config/core", "config_show"),
        ("PUT", "/api/config/core", "config_save"),
    ]

    for method, path, expected in expected_routes:
        route_name, _params = match_route(method, path)
        assert route_name == expected, f"{method} {path} should resolve to {expected}, got {route_name!r}"


def test_web_client_uses_acp_and_git_surface_expected_by_backend() -> None:
    source = _read("src/ui/src/lib/api.ts")
    bash_source = _read("src/ui/src/lib/api/bash.ts")

    expected_fragments = [
        "/api/quests/${questId}/session",
        "/api/quests/${questId}/settings",
        "format=acp",
        "session_id=quest:${questId}",
        "stream=1",
        "/api/quests/${questId}/workflow",
        "/api/quests/${questId}/artifacts",
        "/api/quests/${questId}/node-traces",
        "/api/quests/${questId}/explorer",
        "/api/quests/${questId}/memory",
        "/api/quests/${questId}/documents",
        "/api/quests/${questId}/graph",
        "/api/quests/${questId}/git/branches",
        "/api/quests/${questId}/git/log",
        "/api/quests/${questId}/git/compare",
        "/api/quests/${questId}/git/commit",
        "/api/quests/${questId}/git/diff-file",
        "/api/quests/${questId}/git/commit-file",
        "/api/quests/${questId}/documents/open",
        "/api/quests/${questId}/documents/assets",
        "/api/quests/${questId}/chat",
        "/api/quests/${questId}/commands",
        "/api/quests/${questId}/runs",
        "/api/config/files",
        "/api/config/${name}",
    ]

    for fragment in expected_fragments:
        assert fragment in source, f"Web API client is missing contract fragment: {fragment}"

    bash_fragments = [
        "/api/quests/${projectId}/bash/sessions",
        "/api/quests/${projectId}/bash/sessions/${bashId}",
        "/api/quests/${projectId}/bash/sessions/${bashId}/logs",
        "/api/quests/${projectId}/bash/sessions/${bashId}/stop",
    ]

    for fragment in bash_fragments:
        assert fragment in bash_source, f"Bash API client is missing contract fragment: {fragment}"


def test_web_workspace_keeps_streaming_operational_views_and_tool_effect_surface() -> None:
    acp_source = _read("src/ui/src/lib/acp.ts")
    feed_source = _read("src/ui/src/components/EventFeed.tsx")
    tool_ops_source = _read("src/ui/src/lib/toolOperations.ts")
    workspace_source = _read("src/ui/src/components/WorkflowStudio.tsx")
    bash_tool_source = _read("src/ui/src/components/workspace/QuestBashExecOperation.tsx")

    assert "item.type === 'message' && item.stream && item.role === 'assistant'" in acp_source
    assert "window.setTimeout" in acp_source
    assert "client.workflow(targetQuestId)" in acp_source
    assert "client.explorer(targetQuestId)" in acp_source
    assert "function OperationBlock" in feed_source
    assert "item.type === 'operation'" in feed_source
    assert "ACP-compatible copilot events will appear here." in feed_source
    assert "buildToolEffectPreviews" in tool_ops_source
    assert "<EventFeed questId={questId} items={feed} />" in workspace_source
    assert "McpBashExecView" in bash_tool_source


def test_tui_client_and_git_canvas_follow_same_protocol_contract() -> None:
    tui_source = _read("src/tui/src/lib/api.ts")
    tui_app_source = _read("src/tui/src/app/AppContainer.tsx")
    tui_history_source = _read("src/tui/src/components/HistoryItemDisplay.tsx")
    tui_bash_source = _read("src/tui/src/components/messages/BashExecOperationMessage.tsx")
    app_source = _read("src/ui/src/App.tsx")
    workspace_source = _read("src/ui/src/pages/ProjectWorkspacePage.tsx")
    canvas_source = _read("src/ui/src/components/git/GitResearchCanvas.tsx")

    tui_fragments = [
        "/api/quests/${questId}/events?after=${cursor}&format=acp&session_id=quest:${questId}",
        "text/event-stream",
        "stream=1",
        "/api/connectors",
        "/api/quests/${questId}/chat",
        "/api/quests/${questId}/commands",
        "/api/quests/${questId}/control",
        "/api/quests/${questId}/bash/sessions/${bashId}",
        "/api/quests/${questId}/bash/sessions/${bashId}/logs",
        "/api/quests/${questId}/bash/sessions/${bashId}/stream",
    ]
    canvas_fragments = [
        "/api/quests/${questId}/graph/svg",
        ".gitBranches(",
        ".gitCompare(",
        ".gitLog(",
        ".gitCommit(",
        ".gitDiffFile(",
        ".gitCommitFile(",
    ]

    for fragment in tui_fragments:
        assert fragment in tui_source, f"TUI API client is missing contract fragment: {fragment}"

    for fragment in canvas_fragments:
        assert fragment in canvas_source, f"Git canvas is missing contract fragment: {fragment}"

    assert "target.pathname = `/projects/${questId}`" in tui_app_source
    assert "client.configFiles(baseUrl)" in tui_app_source
    assert "stringifyStructured" in tui_app_source
    assert "openConfigBrowser" in tui_app_source
    assert "searchParams.set('quest'" not in tui_app_source
    assert "BashExecOperationMessage" in tui_history_source
    assert " | " in tui_bash_source
    assert "streamBashLogs" in tui_bash_source
    assert '/settings/:configName' in app_source
    assert "WorkspaceLayout" in workspace_source
    assert "projectId={projectId}" in workspace_source


def test_local_quest_workspace_uses_real_canvas_and_details_tabs() -> None:
    workspace_source = _read("src/ui/src/components/workspace/WorkspaceLayout.tsx")
    surface_source = _read("src/ui/src/components/workspace/QuestWorkspaceSurface.tsx")

    expected_workspace_fragments = [
        "const QUEST_WORKSPACE_PLUGIN_ID = '@ds/plugin-quest-workspace'",
        "buildQuestWorkspaceTabContext(projectId, view)",
        "openQuestWorkspaceTab('canvas')",
        "openQuestWorkspaceTab('details')",
        "openQuestWorkspaceTab('terminal')",
        "title: getQuestWorkspaceTitle(view)",
        "return getQuestWorkspaceTabView(resolvedTab)",
        "view={resolvedQuestWorkspaceView}",
    ]
    for fragment in expected_workspace_fragments:
        assert fragment in workspace_source, f"Quest workspace tabs should include: {fragment}"

    expected_surface_fragments = [
        "view: controlledView",
        "onViewChange",
        "const view = controlledView ?? uncontrolledView",
        "view === 'canvas' ? (",
        "view === 'terminal' ? (",
        "<QuestCanvasSurface",
        "<QuestTerminalSurface",
        "<QuestDetails",
    ]
    for fragment in expected_surface_fragments:
        assert fragment in surface_source, f"Quest surface should stay tab-controlled with: {fragment}"


def test_workspace_surfaces_hide_autofigure_entry_points() -> None:
    marketplace_source = _read("src/ui/src/lib/plugins/marketplace/MarketplacePlugin.tsx")
    workspace_source = _read("src/ui/src/components/workspace/WorkspaceLayout.tsx")

    assert 'title: "AutoFigure"' not in marketplace_source
    assert "tab.pluginId === BUILTIN_PLUGINS.AUTOFIGURE" in workspace_source
