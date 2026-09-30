from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .artifacts import END, START, block_merge, read_text
from .config import load_settings, yaml_load
from .detection import LANGUAGES, detect
from .files import discover_files, git_run, is_git
from .global_setup import GLOBAL_POLICY, codex_home, compatible_entry, read_codex_config, serena_entries
from .mcp_client import MCPClient
from .models import ContextError
from .repo_map import build_map, estimated_tokens, map_status
from .serena import Serena, run


def has_block(path: Path) -> bool:
    if not path.is_file():
        return False
    text = read_text(path)
    if START not in text or END not in text:
        return False
    try:
        block_merge(text, "probe")
        return True
    except ContextError:
        return False


def connect(root: Path, serena: Serena) -> str:
    if not serena.executable or not serena.compatible:
        raise ContextError("Cannot connect: Serena unavailable or incompatible")
    if not (root / ".serena/project.yml").is_file():
        raise ContextError("Cannot connect: Serena project configuration missing; run init")
    # doctor --connect may create Serena caches in the project, but never rewrites source/configuration.
    with tempfile.TemporaryDirectory(prefix="codex-context-connect-") as directory:
        env = dict(os.environ, SERENA_HOME=directory)
        command = [serena.executable, "start-mcp-server", "--context=codex", "--project-from-cwd",
                   "--open-web-dashboard", "false", "--enable-gui-log-window", "false"]
        with MCPClient(command, cwd=root, env=env) as client:
            response = client.initialize()
            tools = client.tools()
            if "find_symbol" not in tools or "find_referencing_symbols" not in tools:
                raise ContextError("Serena did not expose required navigation tools")
            if "activate_project" in tools:
                client.call("activate_project", {"project": str(root)})
            candidates = [p for p in discover_files(root, load_settings(root).ignored_paths)
                          if p.suffix in LANGUAGES]
            if candidates and "get_symbols_overview" in tools:
                # A successful handshake alone cannot prove that the language backend started.
                client.call("get_symbols_overview", {"relative_path": candidates[0].relative_to(root).as_posix()})
            server = response.get("serverInfo", {})
            return f"connected ({server.get('name', 'Serena')}); project activated" + (
                "; symbol overview checked" if candidates and "get_symbols_overview" in tools else "")


def doctor(root: Path, *, check_connection: bool = False) -> tuple[list[str], bool]:
    lines: list[str] = []
    pending = False
    settings = load_settings(root)
    facts = detect(root, settings)
    lines.append("Git: available" if is_git(root) else "Git: no repository (filesystem fallback)")
    if is_git(root):
        tracked_cache = git_run(root, ["ls-files", "-z", "--", ".ai/.cache"])
        if tracked_cache and tracked_cache.stdout:
            lines.append("Cache: files already tracked by Git; remove them from the index manually")
            pending = True
        map_ignore = git_run(root, ["check-ignore", "--no-index", ".ai/repo-map.md"])
        if settings.tracked and map_ignore and map_ignore.returncode == 0:
            lines.append("Map tracking: Git ignores the map despite tracked=true; review ignore rules")
            pending = True
    codex = shutil.which("codex")
    if codex:
        codex_result = run([codex, "--version"])
        lines.append("Codex: " + (codex_result.stdout.strip() if codex_result.returncode == 0 else "not functional"))
        pending |= codex_result.returncode != 0
    else:
        lines.append("Codex: not installed")
        pending = True
    serena = Serena.detect()
    lines.append("Serena: " + (serena.version if serena.compatible else serena.reason))
    if not serena.compatible:
        lines.append("Action: run `codex-context setup-global`")
        pending = True
    home = codex_home()
    entries = serena_entries(read_codex_config(home))
    integration = len(entries) == 1 and compatible_entry(entries[0][1])
    lines.append("Codex/Serena: configured" if integration else "Codex/Serena: pending or conflicting")
    pending |= not integration
    global_block = has_block(home / "AGENTS.md")
    if global_block:
        global_block = GLOBAL_POLICY in read_text(home / "AGENTS.md").replace("\r\n", "\n")
    lines.append("AGENTS global: ok" if global_block else "AGENTS global: missing or outdated managed block")
    pending |= not global_block
    project_block = has_block(root / "AGENTS.md")
    lines.append("AGENTS project: ok" if project_block else "AGENTS project: missing or invalid managed block")
    pending |= not project_block
    if (home / "AGENTS.override.md").is_file() or (root / "AGENTS.override.md").is_file():
        lines.append("AGENTS: override file may shadow managed instructions")
        pending = True
    project_path = root / ".serena/project.yml"
    if project_path.exists():
        data = yaml_load(read_text(project_path), str(project_path))
        valid = isinstance(data.get("language_servers"), list) and isinstance(data.get("project_name"), str)
        lines.append("Serena project: present" if valid else "Serena project: unrecognized schema")
        pending |= not valid
        if data.get("initial_prompt"):
            lines.append("Serena: non-empty initial_prompt increases permanent context")
            pending = True
        if data.get("ignore_all_files_in_gitignore") is not True:
            lines.append("Serena: Git ignores not enabled")
            pending = True
        if valid:
            missing = [language for language in facts.languages if language not in data["language_servers"]]
            if missing:
                lines.append("Serena: language configuration missing " + ", ".join(missing))
                pending = True
    else:
        lines.append("Serena project: pending (run init after setup-global)")
        pending = True
    local_override = root / ".serena/project.local.yml"
    if local_override.is_file():
        values = yaml_load(read_text(local_override), str(local_override))
        if any(values.get(key) not in (None, "", [], True) for key in ("initial_prompt", "language_servers", "ignore_all_files_in_gitignore")):
            lines.append("Serena: project.local.yml overrides context/navigation settings; review effective configuration")
            pending = True
    folder = root / ".serena/memories"
    names = sorted(p.stem for p in folder.glob("*.md")) if folder.is_dir() else []
    lines.append(f"Memories: {len(names)} (lazy; user-owned)")
    if "commands" in names and "project" not in names:
        lines.append("Memories: commands present without project entry point")
    lines.append("Languages: " + (", ".join(facts.languages) or "not detected"))
    if not (root / ".ai/context.yml").is_file():
        lines.append("Context config: missing; run init")
        pending = True
    result = build_map(root, settings)
    status, reason = map_status(root, result)
    lines.append(f"repo map: {status}")
    if reason:
        lines.append("Map detail: " + reason)
    pending |= status != "fresh"
    lines.extend("Extraction: " + message for message in result.errors)
    map_path = root / ".ai/repo-map.md"
    if map_path.exists():
        actual = estimated_tokens(read_text(map_path))
        lines.append(f"Map budget: ~{actual}/{settings.budget_tokens} tokens; tracked={str(settings.tracked).lower()}")
        pending |= actual > settings.budget_tokens
    lines.extend("Detection: " + message for message in facts.warnings)
    if check_connection:
        try:
            lines.append("MCP: " + connect(root, serena))
        except ContextError as exc:
            lines.append("MCP: " + str(exc))
            pending = True
    return lines, pending
