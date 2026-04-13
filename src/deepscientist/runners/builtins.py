from __future__ import annotations

from .claude import ClaudeRunner
from .codex import CodexRunner
from .registry import register_runner


def register_builtin_runners(*, codex_runner: CodexRunner, claude_runner: ClaudeRunner) -> None:
    register_runner("codex", lambda **_: codex_runner)
    register_runner("claude", lambda **_: claude_runner)
