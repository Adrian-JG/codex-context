from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Conflict, ContextError, Ownership

START = "<!-- codex-context:start -->"
END = "<!-- codex-context:end -->"
GENERATED = re.compile(r"\A(?:<!--|#) codex-context:generated sha256=([a-f0-9]{64})(?: -->)?\n")


def read_text(path: Path) -> str:
    if path.is_symlink():
        raise Conflict(f"Refusing to modify symlink: {path}")
    try:
        return path.read_bytes().decode("utf-8") if path.exists() else ""
    except (UnicodeError, OSError) as exc:
        raise ContextError(f"Cannot read {path}: {exc}") from exc


def block_merge(text: str, body: str) -> str:
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise Conflict("Invalid or duplicated codex-context block delimiters")
    newline = "\r\n" if "\r\n" in text else "\n"
    block = START + newline + body.strip().replace("\n", newline) + newline + END
    if START in text:
        start, end = text.index(START), text.index(END) + len(END)
        if end <= start:
            raise Conflict("codex-context block delimiters are reversed")
        return text[:start] + block + text[end:]
    return text + (newline if text.endswith(("\n", "\r")) else newline * 2 if text else "") + block + newline


def generated_text(body: str, *, comment: bool = False) -> str:
    digest = hashlib.sha256(body.encode()).hexdigest()
    header = f"# codex-context:generated sha256={digest}\n" if comment else f"<!-- codex-context:generated sha256={digest} -->\n"
    return header + body


def is_generated(text: str) -> bool:
    match = GENERATED.match(text)
    if match:
        return hashlib.sha256(text[match.end():].encode()).hexdigest() == match[1]
    try:
        data = json.loads(text)
        marker = data.pop("_codex_context_generated")
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return isinstance(marker, str) and hashlib.sha256(canonical.encode()).hexdigest() == marker
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def generated_json(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    marked = dict(data, _codex_context_generated=hashlib.sha256(canonical.encode()).hexdigest())
    return json.dumps(marked, indent=2, sort_keys=True) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = ""
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".codex-context-", delete=False) as stream:
            name = stream.name
            stream.write(content.encode("utf-8"))
        if path.exists():
            os.chmod(name, path.stat().st_mode)
        os.replace(name, path)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


@dataclass
class Change:
    path: Path
    before: str | None
    after: str
    ownership: Ownership


class ChangePlan:
    """Validate all changes before writing; preserve user-owned files even with force."""

    def __init__(self, boundary: Path) -> None:
        self.boundary = boundary.resolve()
        self.changes: list[Change] = []

    def add(self, path: Path, after: str, ownership: Ownership) -> None:
        # Resolve parent components too: an .ai symlink must not escape the repository.
        if not path.resolve().is_relative_to(self.boundary) or path.is_symlink():
            raise Conflict(f"Unsafe artifact path: {path}")
        before = read_text(path) if path.exists() else None
        if ownership == Ownership.CREATE_ONCE_USER_OWNED and before is not None:
            return
        if ownership == Ownership.GENERATED and before is not None and not is_generated(before):
            raise Conflict(f"Preserving unowned or manually edited generated file: {path}")
        if before != after:
            self.changes.append(Change(path, before, after, ownership))

    def apply(self, dry_run: bool = False) -> list[str]:
        lines = [f"{'would create' if dry_run else 'create'} {c.path}" if c.before is None else
                 f"{'would update' if dry_run else 'update'} {c.path}" for c in self.changes]
        if dry_run:
            return lines or ["unchanged"]
        applied: list[Change] = []
        created_dirs: set[Path] = set()
        try:
            for change in self.changes:
                current = read_text(change.path) if change.path.exists() else None
                if current != change.before:
                    raise Conflict(f"File changed during operation: {change.path}")
                parent = change.path.parent
                while not parent.exists() and parent.is_relative_to(self.boundary):
                    created_dirs.add(parent)
                    parent = parent.parent
                atomic_write(change.path, change.after)
                applied.append(change)
        except Exception:
            for change in reversed(applied):
                if change.before is None:
                    change.path.unlink(missing_ok=True)
                else:
                    atomic_write(change.path, change.before)
            for directory in sorted(created_dirs, key=lambda p: len(p.parts), reverse=True):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        return lines or ["unchanged"]
