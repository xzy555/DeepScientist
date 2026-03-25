from __future__ import annotations

from pathlib import Path

from deepscientist import shared


def test_resolve_runner_binary_prefers_env_override_for_codex(monkeypatch, tmp_path: Path) -> None:
    system_codex = tmp_path / "system-codex"
    system_codex.write_text("", encoding="utf-8")
    override_codex = tmp_path / "override-codex"
    override_codex.write_text("", encoding="utf-8")

    def fake_resolve(reference: str) -> str | None:
        if reference == "codex":
            return str(system_codex)
        if reference == str(override_codex):
            return str(override_codex)
        return None

    monkeypatch.setattr(shared, "_resolve_executable_reference", fake_resolve)
    monkeypatch.setenv("DEEPSCIENTIST_CODEX_BINARY", str(override_codex))

    assert shared.resolve_runner_binary("codex", runner_name="codex") == str(override_codex)


def test_resolve_runner_binary_prefers_path_codex_over_bundled_fallback(monkeypatch, tmp_path: Path) -> None:
    system_codex = tmp_path / "system-codex"
    system_codex.write_text("", encoding="utf-8")
    bundled_root = tmp_path / "repo"
    bundled_binary = bundled_root / "node_modules" / ".bin" / "codex"
    bundled_binary.parent.mkdir(parents=True, exist_ok=True)
    bundled_binary.write_text("", encoding="utf-8")

    monkeypatch.delenv("DEEPSCIENTIST_CODEX_BINARY", raising=False)
    monkeypatch.delenv("DS_CODEX_BINARY", raising=False)
    monkeypatch.setattr(
        shared,
        "_resolve_executable_reference",
        lambda reference: str(system_codex) if reference == "codex" else None,
    )
    monkeypatch.setattr(shared, "_codex_repo_roots", lambda: [bundled_root])

    assert shared.resolve_runner_binary("codex", runner_name="codex") == str(system_codex)


def test_resolve_runner_binary_uses_bundled_codex_as_fallback(monkeypatch, tmp_path: Path) -> None:
    bundled_root = tmp_path / "repo"
    bundled_binary = bundled_root / "node_modules" / ".bin" / "codex"
    bundled_binary.parent.mkdir(parents=True, exist_ok=True)
    bundled_binary.write_text("", encoding="utf-8")

    monkeypatch.delenv("DEEPSCIENTIST_CODEX_BINARY", raising=False)
    monkeypatch.delenv("DS_CODEX_BINARY", raising=False)
    monkeypatch.setattr(shared, "_resolve_executable_reference", lambda reference: None)
    monkeypatch.setattr(shared, "_codex_repo_roots", lambda: [bundled_root])

    assert shared.resolve_runner_binary("codex", runner_name="codex") == str(bundled_binary)
