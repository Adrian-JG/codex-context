from __future__ import annotations

from pathlib import Path

from .artifacts import ChangePlan, block_merge, read_text
from .config import default_config, load_settings, merge_defaults, yaml_dump
from .detection import detect
from .models import ContextError, Ownership
from .repo_map import build_map, map_status, plan_map
from .serena import Serena, project_config
from .templates import agents_body, memories


def plan_gitignore(plan: ChangePlan, root: Path, tracked: bool) -> None:
    path = root / ".ai/.gitignore"
    text = read_text(path)
    start, end = "# codex-context:start", "# codex-context:end"
    if text.count(start) != text.count(end) or text.count(start) > 1:
        raise ContextError("Invalid managed .ai/.gitignore delimiters")
    block = start + "\n/.cache/\n" + ("/repo-map.md\n" if not tracked else "!/repo-map.md\n") + end
    if start in text:
        first, last = text.index(start), text.index(end) + len(end)
        if first >= last:
            raise ContextError("Reversed .ai/.gitignore delimiters")
        merged = text[:first] + block + text[last:]
    else:
        merged = text + ("\n" if text and not text.endswith("\n") else "") + block + "\n"
    plan.add(path, merged, Ownership.MANAGED_BLOCK)


def init_project(root: Path, *, dry_run: bool = False, force: bool = False) -> list[str]:
    settings = load_settings(root)
    facts = detect(root, settings)
    plan = ChangePlan(root)
    agents = root / "AGENTS.md"
    plan.add(agents, block_merge(read_text(agents), agents_body(facts)), Ownership.MANAGED_BLOCK)
    context = root / ".ai/context.yml"
    text = merge_defaults(read_text(context)) if context.exists() else yaml_dump(default_config())
    plan.add(context, text, Ownership.STRUCTURED_MERGE)
    plan_gitignore(plan, root, settings.tracked)
    for name, body in memories(facts).items():
        plan.add(root / f".serena/memories/{name}.md", body, Ownership.CREATE_ONCE_USER_OWNED)
    lines = list(facts.warnings)
    serena = Serena.detect()
    if serena.compatible:
        if dry_run:
            lines.append("would generate/merge Serena project config using the installed native schema")
        else:
            try:
                template = serena.native_template()
                path = root / ".serena/project.yml"
                body, warnings = project_config(template, root.name, facts.languages,
                                                read_text(path) if path.exists() else None, settings.ignored_paths)
                plan.add(path, body, Ownership.STRUCTURED_MERGE)
                lines.extend(warnings)
            except ContextError as exc:
                # Independent local setup remains useful even when Serena is unavailable/incompatible.
                lines.append(f"Serena configuration pending: {exc}")
    else:
        lines.extend([f"Serena: {serena.reason}", "Action: run `codex-context setup-global`"])
    result = build_map(root, settings, force=force)
    plan_map(plan, root, result)
    lines.extend(plan.apply(dry_run))
    if not facts.languages:
        lines.append("Language: not detected (empty or unsupported project)")
    return lines


def update_map(root: Path, *, refresh: bool, dry_run: bool = False, force: bool = False) -> list[str]:
    settings = load_settings(root)
    result = build_map(root, settings, force=force)
    plan = ChangePlan(root)
    plan_gitignore(plan, root, settings.tracked)
    status, reason = map_status(root, result)
    # refresh also updates disposable metadata when bodies changed; the map stays untouched.
    plan_map(plan, root, result)
    lines = plan.apply(dry_run)
    if refresh and status == "fresh" and not force:
        return ["repo map: fresh", *lines]
    return [f"repo map: {'would refresh' if dry_run else 'fresh'}" + (f" ({reason})" if reason else ""), *lines]
