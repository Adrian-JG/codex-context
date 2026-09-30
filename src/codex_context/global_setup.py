from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import tomlkit

from .artifacts import ChangePlan, block_merge, read_text
from .models import Conflict, ContextError, Ownership
from .serena import INSTALL_COMMAND, Serena, install_serena, run

GLOBAL_POLICY = """Use Serena for repo navigation; activate the target project if needed.
Relevant memory → symbols → references → targeted code → repo map → broad search.
Never load all memories or whole files for discovery.
Use .ai/repo-map.md only for architecture/cross-cutting work.
Persist only durable, non-obvious knowledge; avoid duplication."""


def codex_home() -> Path:
    override = os.environ.get("CODEX_HOME", "").strip()
    return Path(override).expanduser().resolve() if override else Path.home() / ".codex"


def read_codex_config(home: Path) -> Any:
    try:
        return tomlkit.parse(read_text(home / "config.toml"))
    except ValueError as exc:
        raise ContextError(f"Invalid Codex config.toml: {exc}") from exc


def serena_entries(data: Any) -> list[tuple[str, Any]]:
    entries: list[tuple[str, Any]] = []
    servers = data.get("mcp_servers", {})
    for name, value in servers.items():
        command = str(value.get("command", ""))
        args = value.get("args", [])
        if "serena" in name.lower() or "serena" in command.lower() or any("serena" in str(a).lower() for a in args):
            entries.append((name, value))
    return entries


def compatible_entry(value: Any) -> bool:
    args = [str(a) for a in value.get("args", [])]
    context = "--context=codex" in args or any(a == "--context" and i + 1 < len(args) and args[i + 1] == "codex"
                                              for i, a in enumerate(args))
    return value.get("enabled", True) and "start-mcp-server" in args and "--project-from-cwd" in args and context


def setup_global(*, dry_run: bool = False, install: bool = False) -> list[str]:
    home = codex_home()
    original = read_codex_config(home)
    agents = read_text(home / "AGENTS.md")
    merged_agents = block_merge(agents, GLOBAL_POLICY)
    codex = shutil.which("codex")
    if not codex or run([codex, "--version"]).returncode:
        raise ContextError("Codex CLI is not available; install Codex before setup-global")
    entries = serena_entries(original)
    if len(entries) > 1:
        raise Conflict("Multiple Serena MCP entries found; preserve and resolve them manually")
    if entries and not compatible_entry(entries[0][1]):
        raise Conflict("Existing Serena MCP entry is incompatible; preserve and resolve it manually")
    serena = Serena.detect()
    if not serena.executable:
        if not install:
            raise ContextError(f"Serena: not installed. Install with `{INSTALL_COMMAND}` or use setup-global --install-serena. No configuration changed.")
        if dry_run:
            return [f"would install: {INSTALL_COMMAND}", "would verify Serena and configure Codex; no files changed"]
        serena = install_serena()
    if not serena.compatible:
        raise ContextError("Serena is incompatible: " + serena.reason)
    if dry_run:
        return ["would verify/configure Serena MCP", "would update managed global AGENTS block; no files changed"]
    executable = serena.executable
    assert executable is not None
    with tempfile.TemporaryDirectory(prefix="codex-context-global-") as directory:
        stage = Path(directory)
        staged_home = stage / "codex"
        staged_home.mkdir()
        env = dict(os.environ, CODEX_HOME=str(staged_home), SERENA_HOME=str(stage / "serena"))
        (staged_home / "config.toml").write_bytes(tomlkit.dumps(original).encode("utf-8"))
        if not entries:
            setup_help = run([executable, "setup", "--help"], env=env)
            if setup_help.returncode == 0 and "codex" in setup_help.stdout:
                result = run([executable, "setup", "codex"], env=env)
            else:
                result = run([codex, "mcp", "add", "serena", "--", executable,
                              "start-mcp-server", "--project-from-cwd", "--context=codex"], env=env)
            if result.returncode:
                raise ContextError("Official Serena/Codex setup failed; real configuration untouched: " + result.stderr[-1000:])
            staged = read_codex_config(staged_home)
            entry = staged.get("mcp_servers", {}).get("serena")
            if entry is None or not compatible_entry(entry):
                raise ContextError("Official setup did not produce a compatible Serena entry")
            # Resolve the executable so desktop clients do not depend on a shell's PATH.
            entry["command"] = executable
            entry.setdefault("startup_timeout_sec", 15)
            if "mcp_servers" not in original:
                original["mcp_servers"] = tomlkit.table()
            original["mcp_servers"]["serena"] = entry
        staged_config = tomlkit.dumps(original)
        (staged_home / "config.toml").write_bytes(staged_config.encode("utf-8"))
        server_name = entries[0][0] if entries else "serena"
        verified = run([codex, "mcp", "get", server_name, "--json"], env=env)
        if verified.returncode:
            raise ContextError("Codex could not verify the staged MCP configuration")
        plan = ChangePlan(home)
        plan.add(home / "config.toml", staged_config, Ownership.STRUCTURED_MERGE)
        plan.add(home / "AGENTS.md", merged_agents, Ownership.MANAGED_BLOCK)
        lines = plan.apply()
    if (home / "AGENTS.override.md").is_file():
        lines.append("Warning: global AGENTS.override.md may shadow AGENTS.md")
    return ["Serena MCP: configured (connection checked separately by doctor --connect)", *lines]
