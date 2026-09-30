from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ContextError(Exception):
    """An actionable operational error."""


class ConfigError(ContextError):
    """Invalid user configuration."""


class Conflict(ContextError):
    """Applying a change would overwrite user-owned content."""


class Ownership(StrEnum):
    GENERATED = "GENERATED"
    MANAGED_BLOCK = "MANAGED_BLOCK"
    STRUCTURED_MERGE = "STRUCTURED_MERGE"
    CREATE_ONCE_USER_OWNED = "CREATE_ONCE_USER_OWNED"


@dataclass
class ProjectFacts:
    root: Path
    files: list[Path]
    languages: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    purpose: str = ""
    profile: str = "base"
    durable_notes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Settings:
    budget_tokens: int = 1000
    tracked: bool = True
    ignored_paths: list[str] = field(default_factory=list)
    profile: str = "auto"
    extractors: str | list[str] = "auto"


@dataclass(frozen=True)
class Symbol:
    name: str
    text: str
    kind: str


@dataclass
class Extraction:
    path: str
    language: str
    module: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    structure: Any = None
    error: str = ""


@dataclass
class MapResult:
    content: str
    cache: dict[str, Any]
    errors: list[str]
    changed_structure: bool
