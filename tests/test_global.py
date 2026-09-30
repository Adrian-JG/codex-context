import os
import subprocess
from pathlib import Path

import pytest
import tomlkit

from codex_context import global_setup
from codex_context.artifacts import START
from codex_context.models import Conflict, ContextError
from codex_context.serena import Serena


@pytest.fixture
def fake_codex_serena(monkeypatch):
    monkeypatch.setattr(global_setup.shutil, "which", lambda name: name if name in {"codex", "serena"} else None)
    monkeypatch.setattr(Serena, "detect", classmethod(lambda cls: Serena("serena", "Serena 1.7.0", True)))
    calls = []

    def fake_run(command, *, env=None, **kwargs):
        calls.append(command)
        stdout = ""
        if command[1:] == ["--version"]:
            stdout = "codex-cli 0.159.2"
        elif command[1:] == ["setup", "--help"]:
            stdout = "setup codex"
        elif command[1:] == ["setup", "codex"]:
            path = Path(env["CODEX_HOME"]) / "config.toml"
            assert b"\r\r\n" not in path.read_bytes(), "Staging must preserve Windows CRLF"
            data = tomlkit.parse(path.read_text())
            if "mcp_servers" not in data:
                data["mcp_servers"] = tomlkit.table()
            data["mcp_servers"]["serena"] = {"command": "serena", "args": ["start-mcp-server", "--context=codex", "--project-from-cwd"]}
            path.write_text(tomlkit.dumps(data))
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(global_setup, "run", fake_run)
    return calls


def test_existing_codex_settings_preserved(fake_codex_serena):
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir()
    path = home / "config.toml"
    path.write_text('# Important comment\nmodel = "custom"\n[mcp_servers.existing]\ncommand = "existing-server"\nargs = ["--custom"]\n')
    agents = home / "AGENTS.md"
    agents.write_text("Manual global guidance.\n")
    global_setup.setup_global()
    data = tomlkit.parse(path.read_text())
    assert data["model"] == "custom" and data["mcp_servers"]["existing"]["args"] == ["--custom"]
    assert "# Important comment" in path.read_text()
    assert agents.read_text().startswith("Manual global guidance.\n")
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in home.iterdir()}
    global_setup.setup_global()
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in home.iterdir()} == before
    assert agents.read_text().count(START) == 1
    assert sum(call[1:] == ["setup", "codex"] for call in fake_codex_serena) == 1


def test_missing_serena_leaves_no_partial_configuration(monkeypatch, fake_codex_serena):
    monkeypatch.setattr(Serena, "detect", classmethod(lambda cls: Serena(None, reason="not installed")))
    home = Path(os.environ["CODEX_HOME"])
    with pytest.raises(ContextError, match="No configuration changed"):
        global_setup.setup_global()
    assert not home.exists()


def test_global_dry_run_no_side_effects(fake_codex_serena):
    home = Path(os.environ["CODEX_HOME"])
    global_setup.setup_global(dry_run=True)
    assert not home.exists()
    assert not any(call[1:] == ["setup", "codex"] for call in fake_codex_serena)


def test_install_flag_is_explicit_and_dry_run_does_not_install(monkeypatch, fake_codex_serena):
    monkeypatch.setattr(Serena, "detect", classmethod(lambda cls: Serena(None, reason="not installed")))
    installed = []
    monkeypatch.setattr(global_setup, "install_serena", lambda: installed.append(True) or Serena("serena", "1.7.0", True))
    global_setup.setup_global(dry_run=True, install=True)
    assert not installed
    global_setup.setup_global(install=True)
    assert installed == [True]


def test_incompatible_entry_is_preserved(fake_codex_serena):
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir()
    path = home / "config.toml"
    path.write_text('[mcp_servers.serena]\ncommand = "serena"\nargs = ["manual"]\n')
    before = path.read_bytes()
    with pytest.raises(Conflict):
        global_setup.setup_global()
    assert path.read_bytes() == before
    assert not (home / "AGENTS.md").exists()


def test_global_verification_failure_preserves_real_config(monkeypatch, fake_codex_serena):
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir()
    path = home / "config.toml"
    path.write_text('model = "preserve"\n')
    real_run = global_setup.run

    def failure(command, **kwargs):
        if command[1:3] == ["mcp", "get"]:
            return subprocess.CompletedProcess(command, 1, "", "simulated verification failure")
        return real_run(command, **kwargs)

    monkeypatch.setattr(global_setup, "run", failure)
    with pytest.raises(ContextError, match="verify"):
        global_setup.setup_global()
    assert path.read_text() == 'model = "preserve"\n'
    assert not (home / "AGENTS.md").exists()
