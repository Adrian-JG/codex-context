from __future__ import annotations

import configparser
import json
import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .files import discover_files
from .models import ProjectFacts, Settings

LANGUAGES = {".py": "python", ".pyi": "python", ".js": "typescript", ".jsx": "typescript",
             ".ts": "typescript", ".tsx": "typescript", ".rs": "rust", ".go": "go", ".java": "java",
             ".kt": "kotlin", ".cs": "csharp", ".rb": "ruby", ".php": "php", ".c": "cpp", ".h": "cpp",
             ".cpp": "cpp", ".swift": "swift", ".sh": "bash", ".ps1": "powershell", ".vue": "vue",
             ".svelte": "svelte", ".dart": "dart", ".ex": "elixir", ".zig": "zig"}


def document(path: Path, kind: str) -> dict[str, Any]:
    if path.stat().st_size > 1_000_000:
        return {}
    text = path.read_text(encoding="utf-8-sig")
    data = json.loads(text) if kind == "json" else tomllib.loads(text)
    return data if isinstance(data, dict) else {}


def python_profile(facts: ProjectFacts, manifests: dict[str, dict[str, Any]]) -> None:
    pyproject = manifests.get("pyproject.toml", {})
    tool = pyproject.get("tool", {})
    project = pyproject.get("project", {})
    dependencies = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        dependencies.extend(group)
    for group in pyproject.get("dependency-groups", {}).values():
        dependencies.extend(d for d in group if isinstance(d, str))
    poetry = tool.get("poetry", {})
    dependencies.extend(poetry.get("dependencies", {}))
    for group in poetry.get("group", {}).values():
        dependencies.extend(group.get("dependencies", {}))
    for path in facts.files:
        if path.name.startswith("requirements") and path.suffix == ".txt":
            dependencies.extend(path.read_text(encoding="utf-8-sig", errors="replace").splitlines())
    names = {re.split(r"[\s\[<>=!~;]", d.strip().lower())[0] for d in dependencies if isinstance(d, str)}
    prefix = "uv run " if (facts.root / "uv.lock").exists() else ""
    for name, command, config in [
        ("pytest", "python -m pytest", "pytest"),
        ("ruff", "ruff check .", "ruff"),
        ("mypy", "mypy .", "mypy"),
    ]:
        if name in names or config in tool:
            facts.tools.append(name)
            category = {"pytest": "test", "ruff": "lint", "mypy": "typecheck"}[name]
            # Respect mypy's configured targets instead of overriding them with '.'.
            if name == "mypy" and tool.get("mypy", {}).get("files"):
                command = "mypy"
            facts.commands[category] = prefix + command
    for framework in ("fastapi", "django", "flask", "playwright"):
        if framework in names:
            facts.frameworks.append(framework)
    if not facts.purpose:
        facts.purpose = str(project.get("description") or poetry.get("description") or "")
    if "build-system" in pyproject:
        facts.tools.extend(str(x).split("[", 1)[0].split(">", 1)[0] for x in pyproject["build-system"].get("requires", []))
    if (facts.root / "pytest.ini").exists():
        facts.commands.setdefault("test", prefix + "python -m pytest")
    if (facts.root / "setup.cfg").exists():
        setup_config = configparser.ConfigParser()
        setup_config.read(facts.root / "setup.cfg", encoding="utf-8")
        if setup_config.has_section("tool:pytest"):
            facts.commands.setdefault("test", prefix + "python -m pytest")
    if (facts.root / "tox.ini").exists():
        facts.tools.append("tox")
        facts.commands["validate"] = prefix + "tox"
    if (facts.root / "uv.lock").exists():
        facts.tools.append("uv")


def node_profile(facts: ProjectFacts, manifests: dict[str, dict[str, Any]]) -> None:
    package = manifests.get("package.json", {})
    manager = str(package.get("packageManager", "")).split("@", 1)[0]
    if manager not in {"npm", "pnpm", "yarn", "bun"}:
        manager = next((m for file, m in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"),
                                          ("bun.lock", "bun"), ("bun.lockb", "bun"))
                        if (facts.root / file).exists()), "npm")
    facts.tools.append(manager)
    scripts = package.get("scripts", {})
    for category in ("test", "lint", "typecheck", "build"):
        if category in scripts:
            facts.commands[category] = f"{manager} run {category}"
    dependencies = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
    for dependency, name in (("next", "Next.js"), ("react", "React"), ("vue", "Vue"),
                             ("svelte", "Svelte"), ("@playwright/test", "Playwright")):
        if dependency in dependencies:
            facts.frameworks.append(name)
    if not facts.purpose:
        facts.purpose = str(package.get("description", ""))


# Adding a stack requires a detector here; extractors remain independently registered.
DETECTORS: dict[str, Callable[[ProjectFacts, dict[str, dict[str, Any]]], None]] = {
    "python": python_profile, "node": node_profile,
}


def durable_notes(facts: ProjectFacts) -> None:
    """Create focused pointers only when documentation explicitly establishes a durable topic."""
    topics = {
        "architecture": {"architectural invariants", "invariantes arquitectónicas"},
        "decisions": {"architectural decisions", "decisiones arquitectónicas"},
        "pitfalls": {"known pitfalls", "problemas conocidos"},
        "browser-architecture": {"browser lifecycle", "ciclo de vida del navegador"},
        "resources-performance": {"resource constraints", "restricciones de recursos"},
    }
    for path in facts.files:
        if path.suffix.lower() != ".md" or path.stat().st_size > 64_000:
            continue
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"^#{1,4}\s+(.+?)\s*#*$", line)
            if not match:
                continue
            heading = match[1].strip()
            end = next((j for j in range(index + 1, len(lines)) if lines[j].startswith("#")), len(lines))
            section = "\n".join(lines[index + 1:end]).strip()
            if len(section) < 30:
                continue
            for topic, headings in topics.items():
                if heading.lower() in headings and topic not in facts.durable_notes:
                    if topic.startswith(("browser-", "resources-")) and facts.profile != "python-playwright":
                        continue
                    relative = path.relative_to(facts.root).as_posix()
                    facts.durable_notes[topic] = f"# {heading}\n\nRead `{relative}`, section “{heading}”, for the project's documented {topic.replace('-', ' ')}.\n"


def detect(root: Path, settings: Settings) -> ProjectFacts:
    files = discover_files(root, settings.ignored_paths)
    facts = ProjectFacts(root, files)
    counts: dict[str, int] = {}
    for path in files:
        language = LANGUAGES.get(path.suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + 1
    manifests: dict[str, dict[str, Any]] = {}
    for name, kind in (("pyproject.toml", "toml"), ("package.json", "json"), ("Cargo.toml", "toml")):
        path = root / name
        if path not in files:
            continue
        try:
            manifests[name] = document(path, kind)
            facts.evidence.append(name)
        except (ValueError, OSError) as exc:
            facts.warnings.append(f"Cannot inspect {name}: {exc}")
    if "pyproject.toml" in manifests or any(p.name.startswith("requirements") for p in files):
        counts.setdefault("python", 1)
    if "package.json" in manifests:
        counts.setdefault("typescript", 1)
    if "Cargo.toml" in manifests:
        counts.setdefault("rust", 1)
        facts.tools.append("cargo")
        facts.commands.update(test="cargo test", lint="cargo clippy", build="cargo build")
    if (root / "go.mod") in files:
        counts.setdefault("go", 1)
        facts.tools.append("go")
        facts.commands.update(test="go test ./...", lint="go vet ./...", build="go build ./...")
    facts.languages = sorted(counts, key=lambda language: (-counts[language], language))
    if "python" in counts:
        DETECTORS["python"](facts, manifests)
    if "package.json" in manifests:
        DETECTORS["node"](facts, manifests)
    if "python" in counts:
        facts.profile = "python-playwright" if "playwright" in facts.frameworks else "python"
    elif "typescript" in counts:
        facts.profile = "node"
    if settings.profile != "auto":
        facts.profile = settings.profile
    facts.purpose = " ".join(facts.purpose.split())[:160]
    facts.tools = sorted(set(facts.tools))
    facts.frameworks = sorted(set(facts.frameworks))
    durable_notes(facts)
    return facts
