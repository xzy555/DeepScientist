#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


EVENT_ID_RE = re.compile(rb'"event_id"\s*:\s*"([^"]+)"')
TYPE_RE = re.compile(rb'"(?:type|event_type)"\s*:\s*"([^"]+)"')
RUN_ID_RE = re.compile(rb'"run_id"\s*:\s*"([^"]+)"')
TOOL_NAME_RE = re.compile(rb'"tool_name"\s*:\s*"([^"]+)"')
TIMESTAMP_RE = re.compile(rb'"timestamp"\s*:\s*"([^"]+)"')
SEQ_RE = re.compile(rb'"seq"\s*:\s*(\d+)')
STREAM_RE = re.compile(rb'"stream"\s*:\s*"([^"]+)"')


def _extract(pattern: re.Pattern[bytes], raw: bytes) -> str | None:
    match = pattern.search(raw)
    if match is None:
        return None
    try:
        return match.group(1).decode("utf-8", errors="ignore").strip() or None
    except Exception:
        return None


def _replace_file(path: Path, lines: list[bytes]) -> None:
    with NamedTemporaryFile("wb", delete=False, dir=path.parent, prefix=f"{path.name}.", suffix=".tmp") as handle:
        temp_path = Path(handle.name)
        for line in lines:
            handle.write(line)
    temp_path.replace(path)


def _backup_raw_line(backup_root: Path, *, file_rel: str, line_no: int, raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()[:16]
    safe_rel = file_rel.replace("/", "__")
    backup_name = f"{safe_rel}__line_{line_no:06d}__{digest}.jsonl.gz"
    backup_path = backup_root / backup_name
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(backup_path, "wb") as handle:
        handle.write(raw)
    return str(backup_path)


def _event_placeholder(raw: bytes, *, original_bytes: int, backup_ref: str, file_rel: str, line_no: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_id": _extract(EVENT_ID_RE, raw) or f"evt-slim-{line_no}",
        "type": _extract(TYPE_RE, raw) or "runner.tool_result",
        "run_id": _extract(RUN_ID_RE, raw),
        "tool_name": _extract(TOOL_NAME_RE, raw),
        "status": "compacted",
        "summary": f"Oversized quest event payload ({original_bytes} bytes) was compacted into a quest-local backup.",
        "oversized_event": True,
        "original_bytes": original_bytes,
        "backup_ref": backup_ref,
        "created_at": datetime.now(UTC).isoformat(),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _bash_log_placeholder(raw: bytes, *, original_bytes: int, backup_ref: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "seq": int(_extract(SEQ_RE, raw) or 0),
        "stream": _extract(STREAM_RE, raw) or "stdout",
        "timestamp": _extract(TIMESTAMP_RE, raw),
        "line": f"[compacted oversized bash log entry: {original_bytes} bytes -> {backup_ref}]",
        "oversized_payload": True,
        "original_bytes": original_bytes,
        "backup_ref": backup_ref,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _stdout_placeholder(raw: bytes, *, original_bytes: int, backup_ref: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": _extract(TIMESTAMP_RE, raw),
        "line": f"[compacted oversized stdout entry: {original_bytes} bytes -> {backup_ref}]",
        "oversized_payload": True,
        "original_bytes": original_bytes,
        "backup_ref": backup_ref,
    }
    return {key: value for key, value in payload.items() if value is not None}


def _codex_history_placeholder(raw: bytes, *, original_bytes: int, backup_ref: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": _extract(TIMESTAMP_RE, raw),
        "event": {
            "type": "oversized_payload",
            "summary": f"Oversized codex history entry ({original_bytes} bytes) was compacted into a quest-local backup.",
            "backup_ref": backup_ref,
            "original_bytes": original_bytes,
        },
    }
    return payload


def _placeholder_for(path: Path, raw: bytes, *, original_bytes: int, backup_ref: str, file_rel: str, line_no: int) -> dict[str, Any]:
    normalized = file_rel.replace("\\", "/")
    if normalized == ".ds/events.jsonl":
        return _event_placeholder(raw, original_bytes=original_bytes, backup_ref=backup_ref, file_rel=file_rel, line_no=line_no)
    if normalized.startswith(".ds/bash_exec/") and normalized.endswith("/log.jsonl"):
        return _bash_log_placeholder(raw, original_bytes=original_bytes, backup_ref=backup_ref)
    if normalized.startswith(".ds/runs/") and normalized.endswith("/stdout.jsonl"):
        return _stdout_placeholder(raw, original_bytes=original_bytes, backup_ref=backup_ref)
    if normalized.startswith(".ds/codex_history/") and normalized.endswith("/events.jsonl"):
        return _codex_history_placeholder(raw, original_bytes=original_bytes, backup_ref=backup_ref)
    return {
        "oversized_payload": True,
        "original_bytes": original_bytes,
        "backup_ref": backup_ref,
    }


def _iter_target_files(ds_root: Path) -> list[Path]:
    files: list[Path] = []
    direct_events = ds_root / "events.jsonl"
    if direct_events.exists():
        files.append(direct_events)
    files.extend(sorted((ds_root / "bash_exec").glob("**/log.jsonl")))
    files.extend(sorted((ds_root / "codex_history").glob("**/events.jsonl")))
    files.extend(sorted((ds_root / "runs").glob("**/stdout.jsonl")))
    return [path for path in files if path.is_file()]


def slim_quest_jsonl(quest_root: Path, *, threshold_bytes: int) -> dict[str, Any]:
    ds_root = quest_root / ".ds"
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_root = ds_root / "slim_backups" / timestamp
    manifest: dict[str, Any] = {
        "quest_root": str(quest_root),
        "threshold_bytes": threshold_bytes,
        "backup_root": str(backup_root),
        "processed_at": datetime.now(UTC).isoformat(),
        "files": [],
        "compacted_line_count": 0,
        "compacted_bytes_total": 0,
    }

    for path in _iter_target_files(ds_root):
        rel = path.relative_to(quest_root).as_posix()
        line_no = 0
        compacted_lines = 0
        compacted_bytes = 0
        rewritten: list[bytes] = []
        with path.open("rb") as handle:
            for raw in handle:
                line_no += 1
                line_bytes = len(raw)
                if line_bytes <= threshold_bytes:
                    rewritten.append(raw)
                    continue
                backup_ref = _backup_raw_line(backup_root, file_rel=rel, line_no=line_no, raw=raw)
                placeholder = _placeholder_for(
                    path,
                    raw,
                    original_bytes=line_bytes,
                    backup_ref=Path(backup_ref).relative_to(quest_root).as_posix(),
                    file_rel=rel,
                    line_no=line_no,
                )
                rewritten.append((json.dumps(placeholder, ensure_ascii=False) + "\n").encode("utf-8"))
                compacted_lines += 1
                compacted_bytes += line_bytes
        if compacted_lines:
            _replace_file(path, rewritten)
            manifest["files"].append(
                {
                    "path": rel,
                    "compacted_lines": compacted_lines,
                    "compacted_bytes": compacted_bytes,
                }
            )
            manifest["compacted_line_count"] += compacted_lines
            manifest["compacted_bytes_total"] += compacted_bytes

    if manifest["compacted_line_count"]:
        backup_root.mkdir(parents=True, exist_ok=True)
        manifest_path = backup_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest["manifest_path"] = str(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Compact oversized quest JSONL payloads into quest-local backups.")
    parser.add_argument("quest_root", help="Absolute quest root path")
    parser.add_argument("--threshold-mb", type=int, default=8, help="Compact lines larger than this many MB")
    args = parser.parse_args()

    quest_root = Path(args.quest_root).expanduser().resolve()
    threshold_bytes = max(1, int(args.threshold_mb)) * 1024 * 1024
    manifest = slim_quest_jsonl(quest_root, threshold_bytes=threshold_bytes)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
