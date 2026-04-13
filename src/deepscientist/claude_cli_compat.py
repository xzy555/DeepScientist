from __future__ import annotations

import json
import shutil
from pathlib import Path

from .shared import ensure_dir, write_text


def _remove_tree_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)


def materialize_claude_runtime_home(
    *,
    source_home: str | Path,
    target_home: str | Path,
) -> None:
    source_root = Path(source_home).expanduser()
    target_root = ensure_dir(Path(target_home))
    target_claude_root = target_root / ".claude"

    if target_claude_root.exists() or target_claude_root.is_symlink():
        _remove_tree_path(target_claude_root)

    if source_root.exists() and source_root.is_dir():
        shutil.copytree(source_root, target_claude_root, dirs_exist_ok=True)
    else:
        ensure_dir(target_claude_root)

    source_user_config = source_root.parent / ".claude.json"
    target_user_config = target_root / ".claude.json"
    if source_user_config.exists():
        shutil.copy2(source_user_config, target_user_config)
    else:
        write_text(target_user_config, json.dumps({"hasCompletedOnboarding": True}))
