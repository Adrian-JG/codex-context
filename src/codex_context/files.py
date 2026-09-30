from __future__ import annotations

import subprocess
from pathlib import Path

from pathspec import GitIgnoreSpec

from .models import ContextError

EXCLUDED_DIRS = {".git", ".ai", ".serena", ".venv", "venv", "env", "node_modules", "__pycache__",
                 ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist", "target", "coverage",
                 ".next", ".nuxt", ".tox", ".cache", "vendor", "site-packages"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".dll", ".exe", ".so", ".dylib", ".png", ".jpg", ".jpeg", ".gif",
                     ".pdf", ".zip", ".gz", ".woff", ".woff2", ".mp4", ".sqlite", ".db", ".ico", ".lock"}


def git_run(root: Path, args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, timeout=20)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        raise ContextError("Git file discovery timed out") from exc


def is_git(root: Path) -> bool:
    result = git_run(root, ["rev-parse", "--is-inside-work-tree"])
    return bool(result and result.returncode == 0 and result.stdout.strip() == b"true")


def eligible(root: Path, path: Path, extra: GitIgnoreSpec) -> bool:
    relative = path.relative_to(root)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return False
    if any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in relative.parts[:-1]):
        return False
    if path.name in {"AGENTS.md", "AGENTS.override.md", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}:
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES or path.name.endswith((".min.js", ".map", "_pb2.py")):
        return False
    if extra.match_file(relative.as_posix()) or path.stat().st_size > 1_000_000:
        return False
    try:
        with path.open("rb") as stream:
            sample = stream.read(8192)
        return b"\0" not in sample
    except OSError:
        return False


def discover_files(root: Path, ignored: list[str] | None = None) -> list[Path]:
    extra = GitIgnoreSpec.from_lines(ignored or [])
    if is_git(root):
        names: set[str] = set()
        for args in (["ls-files", "-z", "--cached"], ["ls-files", "-z", "--others", "--exclude-standard"]):
            result = git_run(root, list(args))
            if result is None or result.returncode:
                raise ContextError("Unable to enumerate Git files")
            names.update(n.decode("utf-8", errors="surrogateescape") for n in result.stdout.split(b"\0") if n)
        ordered = sorted(names)
        if ordered:
            result = git_run(root, ["check-ignore", "--no-index", "--stdin", "-z"],
                             b"\0".join(n.encode("utf-8", errors="surrogateescape") for n in ordered) + b"\0")
            if result is None or result.returncode not in (0, 1):
                raise ContextError("Unable to apply Git ignore rules")
            skipped = {n.decode("utf-8", errors="surrogateescape") for n in result.stdout.split(b"\0") if n}
            ordered = [n for n in ordered if n not in skipped]
        return [root / name for name in ordered if eligible(root, root / name, extra)]

    found: list[Path] = []

    def walk(directory: Path, rules: list[tuple[Path, GitIgnoreSpec]]) -> None:
        ignore = directory / ".gitignore"
        local_rules = list(rules)
        if ignore.is_file() and not ignore.is_symlink():
            local_rules.append((directory, GitIgnoreSpec.from_lines(ignore.read_text(encoding="utf-8-sig").splitlines())))
        for path in sorted(directory.iterdir()):
            if path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if extra.match_file(relative + ("/" if path.is_dir() else "")):
                continue
            ignored_here = False
            for base, spec in local_rules:
                check = path.relative_to(base).as_posix() + ("/" if path.is_dir() else "")
                decision = spec.check_file(check).include
                if decision is not None:
                    ignored_here = decision
            if ignored_here:
                continue
            if path.is_dir():
                if path.name not in EXCLUDED_DIRS and not (path / ".git").exists():
                    walk(path, local_rules)
            elif eligible(root, path, extra):
                found.append(path)

    walk(root, [])
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())
