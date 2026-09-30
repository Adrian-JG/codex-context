from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .artifacts import ChangePlan, generated_json, generated_text, is_generated, read_text
from .extractors import extract_file
from .files import discover_files
from .models import Extraction, MapResult, Ownership, Settings, Symbol

CACHE_PATH = ".ai/.cache/structure.json"
FORMAT_VERSION = 1


def estimated_tokens(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / 4)


def json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def read_cache(root: Path) -> dict[str, Any]:
    path = root / CACHE_PATH
    try:
        text = path.read_text(encoding="utf-8")
        if not is_generated(text):
            return {}
        value = json.loads(text)
        return value if isinstance(value, dict) and value.get("format_version") == FORMAT_VERSION else {}
    except (OSError, ValueError):
        return {}


def unpack(data: dict[str, Any]) -> Extraction:
    return Extraction(data["path"], data["language"], data["module"],
                      [Symbol(**s) for s in data["symbols"]], data["imports"], data["structure"], data["error"])


def internal_edges(extractions: list[Extraction]) -> dict[str, set[str]]:
    by_module = {e.module: e.path for e in extractions if e.language == "python" and e.module}
    edges: dict[str, set[str]] = {e.path: set() for e in extractions}
    for extraction in extractions:
        for target in extraction.imports:
            candidate = target
            while candidate:
                if candidate in by_module:
                    destination = by_module[candidate]
                    if destination != extraction.path:
                        edges[extraction.path].add(destination)
                    break
                candidate = candidate.rpartition(".")[0]
    return edges


def render(extractions: list[Extraction], budget: int) -> str:
    edges = internal_edges(extractions)
    indegree = {e.path: 0 for e in extractions}
    for destinations in edges.values():
        for destination in destinations:
            indegree[destination] += 1
    candidates: list[tuple[int, str, str, str]] = []
    for extraction in extractions:
        entry = extraction.path.endswith(("__main__.py", "cli.py", "main.py", "app.py")) or (
            isinstance(extraction.structure, dict) and extraction.structure.get("entrypoint", False)
        )
        base = 20 * int(bool(entry)) + indegree[extraction.path]
        candidates.append((base + 2, extraction.path, "", ""))
        for symbol in extraction.symbols:
            weight = {"class": 10, "function": 8, "method": 3}[symbol.kind]
            # Rendering is bounded independently of the complete structural fingerprint.
            concise = symbol.text if len(symbol.text) <= 240 else symbol.text[:237] + "..."
            candidates.append((base + weight, extraction.path, symbol.name, concise))
        if edges[extraction.path]:
            candidates.append((base + 9, extraction.path, "~imports", "→ " + ", ".join(sorted(edges[extraction.path]))))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    selected: dict[str, list[str]] = {}

    def output(omitted: bool) -> str:
        body = "# Repo Map\n\n"
        for path in sorted(selected):
            symbols = selected[path]
            body += f"- `{path}`" + (": " + "; ".join(symbols) if symbols else "") + "\n"
        if omitted:
            body += "\n… additional structure omitted. Use Serena for details.\n"
        return generated_text(body)

    chosen = 0
    for _, path, _, description in candidates:
        # A previously selected symbol has already included its module path.
        if path in selected and not description:
            chosen += 1
            continue
        existed = path in selected
        selected.setdefault(path, [])
        if description:
            selected[path].append(description)
        if estimated_tokens(output(True)) > budget:
            if description:
                selected[path].pop()
            if not existed:
                del selected[path]
        else:
            chosen += 1
    return output(chosen < len(candidates))


def build_map(root: Path, settings: Settings, *, force: bool = False, verify_hashes: bool = False) -> MapResult:
    old = read_cache(root)
    old_files = old.get("files", {}) if old.get("format_version") == FORMAT_VERSION else {}
    if not isinstance(old_files, dict):
        old_files = {}
    files: dict[str, Any] = {}
    extractions: list[Extraction] = []
    errors: list[str] = []
    for path in discover_files(root, settings.ignored_paths):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        metadata = [stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size]
        previous = old_files.get(relative, {})
        try:
            same_extractor = old.get("settings", {}).get("extractors") == settings.extractors
            if not force and not verify_hashes and same_extractor and previous.get("metadata") == metadata and previous.get("extraction"):
                record = previous
                extraction = unpack(record["extraction"])
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if not force and digest == previous.get("hash") and previous.get("extraction") and same_extractor:
                    extraction = unpack(previous["extraction"])
                else:
                    extracted = extract_file(path, root, settings.extractors)
                    if extracted is None:
                        continue
                    extraction = extracted
                record = {"metadata": metadata, "hash": digest, "extraction": asdict(extraction)}
            files[relative] = record
            extractions.append(extraction)
            if extraction.error:
                errors.append(extraction.error)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{relative}: cannot extract structure: {exc}")
    config = asdict(settings)
    # Fingerprint all eligible extracted structures, including entries trimmed from the map.
    structure = {e.path: {"language": e.language, "module": e.module, "structure": e.structure} for e in extractions}
    fingerprint = hashlib.sha256(json_text({"version": FORMAT_VERSION, "settings": config, "structure": structure}).encode()).hexdigest()
    content = render(extractions, settings.budget_tokens)
    cache = {"format_version": FORMAT_VERSION, "settings": config, "files": files,
             "fingerprint": fingerprint, "errors": errors}
    return MapResult(content, cache, errors, fingerprint != old.get("fingerprint"))


def map_status(root: Path, result: MapResult) -> tuple[str, str]:
    path = root / ".ai/repo-map.md"
    old = read_cache(root)
    if not path.exists():
        return "stale", "map missing"
    if result.errors:
        return "stale", "incomplete extraction"
    if not old:
        return "stale", "local structural cache missing; run refresh"
    if result.changed_structure:
        return "stale", "structural changes"
    if read_text(path) != result.content:
        return "stale", "map differs from current structure"
    return "fresh", ""


def plan_map(plan: ChangePlan, root: Path, result: MapResult) -> None:
    if result.errors:
        from .models import ContextError
        raise ContextError("Map extraction incomplete; existing map preserved:\n" + "\n".join(result.errors))
    plan.add(root / ".ai/repo-map.md", result.content, Ownership.GENERATED)
    body = generated_json(result.cache)
    # Cache is disposable tool-owned data, independently of versionable artifacts.
    cache_path = root / CACHE_PATH
    plan.add(cache_path, body, Ownership.GENERATED)
