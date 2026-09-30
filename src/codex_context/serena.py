from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from .config import yaml_dump, yaml_load
from .models import ContextError

INSTALL_COMMAND = "uv tool install -p 3.13 serena-agent"


def run(command: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None,
        timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, env=env, cwd=cwd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContextError(f"Cannot run {Path(command[0]).name}: {exc}") from exc


@dataclass
class Serena:
    executable: str | None
    version: str = ""
    compatible: bool = False
    reason: str = ""

    @classmethod
    def detect(cls) -> Serena:
        executable = shutil.which("serena")
        if not executable:
            return cls(None, reason="not installed")
        try:
            version = run([executable, "--version"])
            help_result = run([executable, "start-mcp-server", "--help"])
            project_help = run([executable, "project", "create", "--help"])
            required = ("--project-from-cwd", "--context", "--project")
            compatible = version.returncode == help_result.returncode == project_help.returncode == 0 and all(
                flag in help_result.stdout for flag in required
            ) and "--language" in project_help.stdout
            return cls(executable, version.stdout.strip(), compatible,
                       "" if compatible else "required CLI capabilities unavailable")
        except ContextError as exc:
            return cls(executable, reason=str(exc))

    def native_template(self) -> CommentedMap:
        if not self.executable or not self.compatible:
            raise ContextError("Serena is not installed or is incompatible; configuration pending")
        with tempfile.TemporaryDirectory(prefix="codex-context-serena-") as directory:
            stage = Path(directory)
            project = stage / "project"
            project.mkdir()
            # The official CLI bootstraps the schema; no target repository code is executed/scanned.
            (project / "schema_probe.py").write_text("", encoding="utf-8")
            env = dict(os.environ, SERENA_HOME=str(stage / "home"))
            result = run([self.executable, "project", "create", str(project), "--language", "python"], env=env)
            path = project / ".serena/project.yml"
            if result.returncode or not path.exists():
                raise ContextError("Serena could not generate its native schema: " + result.stderr[-1200:])
            template = yaml_load(path.read_text(encoding="utf-8-sig"), "Serena native project schema")
            if not all(key in template for key in ("project_name", "language_servers", "initial_prompt", "ignore_all_files_in_gitignore")):
                raise ContextError("Unsupported Serena project schema; existing configuration preserved")
            return template


def project_config(template: CommentedMap, name: str, languages: list[str], existing: str | None,
                   ignored: list[str]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    supported: set[str] = set()
    # CLI's language validation remains the authority. Template comments provide a local allow-list.
    comment_text = yaml_dump(template)
    for line in comment_text.splitlines():
        if line.lstrip().startswith("#"):
            supported.update(line.lstrip("# ").split())
    known = [language for language in languages if language in supported]
    unknown = [language for language in languages if language not in supported]
    if unknown:
        warnings.append("Serena language support unverified: " + ", ".join(unknown))
    values = {"project_name": name, "language_servers": known, "ignore_all_files_in_gitignore": True,
              "initial_prompt": "", "ignored_paths": ignored}
    if existing is None:
        for key, value in values.items():
            template[key] = value
        return yaml_dump(template), warnings
    data = yaml_load(existing, ".serena/project.yml")
    for key, value in values.items():
        if key not in data:
            data[key] = value
        elif key == "language_servers":
            if not isinstance(data[key], list) or not all(isinstance(x, str) for x in data[key]):
                raise ContextError("Serena language_servers must be a list of strings")
            data[key].extend(language for language in known if language not in data[key])
        elif key == "ignored_paths":
            if not isinstance(data[key], list):
                raise ContextError("Serena ignored_paths must be a list")
            data[key].extend(pattern for pattern in ignored if pattern not in data[key])
    if data.get("initial_prompt"):
        warnings.append("Existing Serena initial_prompt preserved; consider reducing permanent context")
    if data.get("ignore_all_files_in_gitignore") is not True:
        warnings.append("Existing Serena Git ignore setting preserved; Git ignores are disabled")
    original = yaml_load(existing, ".serena/project.yml")
    return existing if original == data else yaml_dump(data), warnings


def install_serena() -> Serena:
    uv = shutil.which("uv")
    if not uv:
        raise ContextError("uv not available. Install uv first: https://docs.astral.sh/uv/getting-started/installation/")
    result = run([uv, "tool", "install", "-p", "3.13", "serena-agent"], timeout=300)
    if result.returncode:
        raise ContextError("Serena installation failed: " + result.stderr[-1200:])
    # uv may install executables in a directory not yet present in this process's PATH.
    if not shutil.which("serena"):
        binary_dir = run([uv, "tool", "dir", "--bin"])
        if binary_dir.returncode == 0:
            os.environ["PATH"] = binary_dir.stdout.strip() + os.pathsep + os.environ.get("PATH", "")
    serena = Serena.detect()
    if not serena.compatible:
        raise ContextError("Serena installed but verification failed: " + serena.reason)
    return serena
