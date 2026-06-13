"""Tests for D056: resolve_markery_root() without a manual MARKERY_ROOT export."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from langgraph_markery import config


def _make_markery(dir_path: Path) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "MANIFEST.json").write_text('{"contract_version": "1.1"}')
    return dir_path


class TestResolveRoot:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("MARKERY_ROOT", "/explicit/path")
        assert config.resolve_markery_root() == "/explicit/path"

    def test_pointer_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MARKERY_ROOT", raising=False)
        markery = _make_markery(tmp_path / "elsewhere" / "markery")
        repo_root = tmp_path / "repo"
        (repo_root / "src" / "langgraph_markery").mkdir(parents=True)
        (repo_root / ".markery-root").write_text(f"# pointer\n{markery}\n")
        fake_config = repo_root / "src" / "langgraph_markery" / "config.py"
        with patch.object(config, "__file__", str(fake_config)):
            assert config.resolve_markery_root() == str(markery.resolve())

    def test_sibling_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MARKERY_ROOT", raising=False)
        markery = _make_markery(tmp_path / "markery")
        repo_root = tmp_path / "markery-langgraph"
        (repo_root / "src" / "langgraph_markery").mkdir(parents=True)
        fake_config = repo_root / "src" / "langgraph_markery" / "config.py"
        with patch.object(config, "__file__", str(fake_config)):
            assert config.resolve_markery_root() == str(markery.resolve())

    def test_nothing_found_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MARKERY_ROOT", raising=False)
        repo_root = tmp_path / "markery-langgraph"
        (repo_root / "src" / "langgraph_markery").mkdir(parents=True)
        fake_config = repo_root / "src" / "langgraph_markery" / "config.py"
        with patch.object(config, "__file__", str(fake_config)):
            assert config.resolve_markery_root() == ""

    def test_pointer_without_manifest_falls_through_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MARKERY_ROOT", raising=False)
        bad = tmp_path / "no-manifest"
        bad.mkdir()
        repo_root = tmp_path / "markery-langgraph"
        (repo_root / "src" / "langgraph_markery").mkdir(parents=True)
        (repo_root / ".markery-root").write_text(str(bad))
        fake_config = repo_root / "src" / "langgraph_markery" / "config.py"
        with patch.object(config, "__file__", str(fake_config)):
            assert config.resolve_markery_root() == ""
