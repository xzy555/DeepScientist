from __future__ import annotations

import re


ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    ("GET", re.compile(r"^/$"), "root"),
    ("GET", re.compile(r"^/ui/(?P<ui_path>.+)$"), "ui_asset"),
    ("GET", re.compile(r"^/(?P<spa_path>(?!api(?:/|$)|ui(?:/|$)|assets(?:/|$)).+)$"), "spa_root"),
    ("GET", re.compile(r"^/api/health$"), "health"),
    ("GET", re.compile(r"^/api/v1/health/cli$"), "cli_health"),
    ("POST", re.compile(r"^/api/admin/shutdown$"), "admin_shutdown"),
    ("GET", re.compile(r"^/api/acp/status$"), "acp_status"),
    ("GET", re.compile(r"^/api/connectors$"), "connectors"),
    ("GET", re.compile(r"^/api/bridges/(?P<connector>[^/]+)/webhook$"), "bridge_webhook"),
    ("POST", re.compile(r"^/api/bridges/(?P<connector>[^/]+)/webhook$"), "bridge_webhook"),
    ("GET", re.compile(r"^/api/connectors/qq/bindings$"), "qq_bindings"),
    ("POST", re.compile(r"^/api/connectors/qq/inbound$"), "qq_inbound"),
    ("GET", re.compile(r"^/api/connectors/(?P<connector>[^/]+)/bindings$"), "connector_bindings"),
    ("POST", re.compile(r"^/api/connectors/(?P<connector>[^/]+)/inbound$"), "connector_inbound"),
    ("GET", re.compile(r"^/api/quests$"), "quests"),
    ("POST", re.compile(r"^/api/quests$"), "quest_create"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)$"), "quest"),
    ("PATCH", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/settings$"), "quest_settings"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/session$"), "quest_session"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/events$"), "quest_events"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/artifacts$"), "quest_artifacts"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/workflow$"), "workflow"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/node-traces$"), "node_traces"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/node-traces/(?P<node_ref>.+)$"), "node_trace"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/history$"), "history"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/bash/sessions$"), "bash_sessions"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/bash/sessions/stream$"), "bash_sessions_stream"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/bash/sessions/(?P<bash_id>[^/]+)$"), "bash_session"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/bash/sessions/(?P<bash_id>[^/]+)/logs$"), "bash_logs"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/bash/sessions/(?P<bash_id>[^/]+)/stream$"), "bash_log_stream"),
    ("POST", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/bash/sessions/(?P<bash_id>[^/]+)/stop$"), "bash_stop"),
    ("POST", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/terminal/session/ensure$"), "terminal_session_ensure"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/terminal/history$"), "terminal_history"),
    ("POST", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/terminal/sessions/(?P<session_id>[^/]+)/input$"), "terminal_input"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/terminal/sessions/(?P<session_id>[^/]+)/restore$"), "terminal_restore"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/terminal/sessions/(?P<session_id>[^/]+)/stream$"), "terminal_stream"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/graph$"), "graph"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/graph/(?P<kind>svg|png|json)$"), "graph_asset"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/metrics/timeline$"), "metrics_timeline"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/git/branches$"), "git_branches"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/git/log$"), "git_log"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/git/compare$"), "git_compare"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/git/commit$"), "git_commit"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/git/diff-file$"), "git_diff_file"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/git/commit-file$"), "git_commit_file"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/runs$"), "runs"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/memory$"), "quest_memory"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/documents$"), "documents"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/explorer$"), "explorer"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/search$"), "quest_search"),
    ("GET", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/documents/asset$"), "document_asset"),
    ("POST", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/documents/open$"), "document_open"),
    ("POST", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/documents/assets$"), "document_asset_upload"),
    ("PUT", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/documents/(?P<document_id>.+)$"), "document_save"),
    ("POST", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/chat$"), "chat"),
    ("POST", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/commands$"), "command"),
    ("POST", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/control$"), "quest_control"),
    ("POST", re.compile(r"^/api/quests/(?P<quest_id>[^/]+)/runs$"), "run_create"),
    ("GET", re.compile(r"^/api/memory$"), "memory"),
    ("GET", re.compile(r"^/api/docs$"), "docs"),
    ("POST", re.compile(r"^/api/docs/open$"), "docs_open"),
    ("GET", re.compile(r"^/api/config/files$"), "config_files"),
    ("GET", re.compile(r"^/api/config/(?P<name>[^/]+)$"), "config_show"),
    ("PUT", re.compile(r"^/api/config/(?P<name>[^/]+)$"), "config_save"),
    ("POST", re.compile(r"^/api/config/validate$"), "config_validate"),
    ("POST", re.compile(r"^/api/config/test$"), "config_test"),
    ("GET", re.compile(r"^/assets/(?P<asset_path>.+)$"), "asset"),
]


def match_route(method: str, path: str) -> tuple[str | None, dict]:
    for route_method, pattern, name in ROUTES:
        if route_method != method:
            continue
        match = pattern.match(path)
        if match:
            return name, match.groupdict()
    return None, {}
