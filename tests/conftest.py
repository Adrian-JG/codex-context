import os
from pathlib import Path

import pytest

from codex_context.serena import Serena


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path, monkeypatch, request):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("SERENA_HOME", str(home / ".serena"))
    if not request.node.get_closest_marker("integration"):
        monkeypatch.setattr(Serena, "detect", classmethod(lambda cls: Serena(None, reason="not installed")))


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repository with spaces"
    path.mkdir()
    return path


@pytest.fixture
def python_repo(repo):
    (repo / "pyproject.toml").write_text('''[project]
name = "example"
version = "0.1.0"
description = "Example application"
dependencies = ["pytest", "ruff", "mypy"]
''', encoding="utf-8")
    package = repo / "src/example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "core.py").write_text('''class Base:
    pass

class Widget(Base):
    def value(self, amount: int = 1) -> int:
        """A deliberately omitted docstring."""
        return amount + 1

async def load(name: str, *, retries: int = 3) -> Widget:
    return Widget()

def _private():
    return 123
''', encoding="utf-8")
    (package / "consumer.py").write_text("from .core import Widget\n\ndef consume():\n    return Widget()\n", encoding="utf-8")
    return repo


def snapshot(root: Path):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def integration_enabled():
    return os.environ.get("CODEX_CONTEXT_INTEGRATION") == "1"
