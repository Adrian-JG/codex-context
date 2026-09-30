import os
import shutil
from pathlib import Path

import pytest
import tomlkit

from codex_context.config import load_settings
from codex_context.doctor import doctor
from codex_context.global_setup import setup_global
from codex_context.mcp_client import MCPClient
from codex_context.project import init_project, update_map
from codex_context.repo_map import build_map, estimated_tokens, map_status
from codex_context.serena import Serena

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.environ.get("CODEX_CONTEXT_INTEGRATION") != "1",
                                                        reason="real integration is explicitly enabled")]


def test_real_codex_serena_end_to_end(repo):
    assert shutil.which("serena"), "Install external Serena in an isolated uv tool environment"
    assert shutil.which("codex"), "Codex CLI is needed; no login/API key required"
    (repo / "pyproject.toml").write_text('[project]\nname="example"\nversion="0.1.0"\ndependencies=["pytest"]\n')
    (repo / "core.py").write_text("class Widget:\n    def value(self, number: int = 1) -> int:\n        return number\n")
    (repo / "consumer.py").write_text("from core import Widget\n\ndef make_widget():\n    return Widget()\n")
    home = Path(os.environ["CODEX_HOME"])
    home.mkdir()
    (home / "config.toml").write_text('# Preserve integration fixture\n[mcp_servers.existing]\ncommand="dummy-server"\n')
    setup_global()
    assert "existing" in tomlkit.parse((home / "config.toml").read_text())["mcp_servers"]
    init_project(repo)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in repo.rglob("*") if p.is_file()}
    init_project(repo)
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in repo.rglob("*") if p.is_file()} == before
    lines, pending = doctor(repo)
    assert not pending, lines
    serena = Serena.detect()
    command = [serena.executable, "start-mcp-server", "--context=codex", "--project-from-cwd",
               "--open-web-dashboard", "false", "--enable-gui-log-window", "false"]
    with MCPClient(command, cwd=repo, env=dict(os.environ), timeout=90) as client:
        assert client.initialize()["serverInfo"]
        tools = client.tools()
        assert {"find_symbol", "find_referencing_symbols"} <= set(tools)
        if "activate_project" in tools:
            client.call("activate_project", {"project": str(repo)})
        schema = tools["find_symbol"]["inputSchema"]["properties"]
        key = "name_path_pattern" if "name_path_pattern" in schema else "name_path"
        symbol = client.call("find_symbol", {key: "Widget", "relative_path": "core.py", "include_body": False})
        assert "Widget" in str(symbol)
        references = client.call("find_referencing_symbols", {"name_path": "Widget", "relative_path": "core.py"})
        assert "consumer.py" in str(references)
    lines, pending = doctor(repo, check_connection=True)
    assert not pending, lines
    source = repo / "core.py"
    source.write_text(source.read_text().replace("return number", "return number + 1"))
    assert map_status(repo, build_map(repo, load_settings(repo)))[0] == "fresh"
    source.write_text(source.read_text().replace("number: int = 1", "number: int = 2"))
    assert map_status(repo, build_map(repo, load_settings(repo)))[0] == "stale"
    update_map(repo, refresh=True)
    assert map_status(repo, build_map(repo, load_settings(repo)))[0] == "fresh"
    assert estimated_tokens((repo / ".ai/repo-map.md").read_text(encoding="utf-8")) <= 1000
