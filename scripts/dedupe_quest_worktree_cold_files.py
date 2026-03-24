#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable


def _sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _iter_targets(worktrees_root: Path, *, min_bytes: int) -> list[Path]:
    patterns = [
        "**/.codex/sessions/**/*.jsonl",
        "**/experiments/**/*.json",
    ]
    files: list[Path] = []
    for pattern in patterns:
        for path in worktrees_root.glob(pattern):
            if not path.is_file():
                continue
            try:
                if path.stat().st_size < min_bytes:
                    continue
            except OSError:
                continue
            files.append(path)
    return sorted(files)


def _replace_with_hardlink(target: Path, source: Path) -> None:
    with NamedTemporaryFile("wb", delete=False, dir=target.parent, prefix=f"{target.name}.", suffix=".linktmp") as handle:
        temp_path = Path(handle.name)
    try:
        temp_path.unlink(missing_ok=True)
        os.link(source, temp_path)
        temp_path.replace(target)
    finally:
        temp_path.unlink(missing_ok=True)


def dedupe_worktree_files(quest_root: Path, *, min_bytes: int) -> dict:
    worktrees_root = quest_root / ".ds" / "worktrees"
    size_buckets: dict[int, list[Path]] = {}
    for path in _iter_targets(worktrees_root, min_bytes=min_bytes):
        try:
            size_buckets.setdefault(path.stat().st_size, []).append(path)
        except OSError:
            continue

    manifest = {
        "quest_root": str(quest_root),
        "worktrees_root": str(worktrees_root),
        "min_bytes": min_bytes,
        "groups_examined": 0,
        "files_relinked": 0,
        "bytes_deduped": 0,
        "groups": [],
    }

    for size, paths in sorted(size_buckets.items(), key=lambda item: -item[0]):
        if len(paths) < 2:
            continue
        manifest["groups_examined"] += 1
        hash_buckets: dict[str, list[Path]] = {}
        for path in paths:
            try:
                hash_buckets.setdefault(_sha256(path), []).append(path)
            except OSError:
                continue
        for digest, dupes in hash_buckets.items():
            if len(dupes) < 2:
                continue
            canonical = dupes[0]
            relinked: list[str] = []
            for duplicate in dupes[1:]:
                try:
                    if canonical.stat().st_ino == duplicate.stat().st_ino:
                        continue
                except OSError:
                    continue
                _replace_with_hardlink(duplicate, canonical)
                manifest["files_relinked"] += 1
                manifest["bytes_deduped"] += size
                relinked.append(str(duplicate.relative_to(quest_root)))
            if relinked:
                manifest["groups"].append(
                    {
                        "sha256": digest,
                        "size_bytes": size,
                        "canonical": str(canonical.relative_to(quest_root)),
                        "relinked": relinked,
                    }
                )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Hardlink-dedupe duplicated large cold files under quest worktrees.")
    parser.add_argument("quest_root", help="Absolute quest root path")
    parser.add_argument("--min-mb", type=int, default=1, help="Only dedupe files at or above this size")
    args = parser.parse_args()

    quest_root = Path(args.quest_root).expanduser().resolve()
    result = dedupe_worktree_files(quest_root, min_bytes=max(1, args.min_mb) * 1024 * 1024)
    import json

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
