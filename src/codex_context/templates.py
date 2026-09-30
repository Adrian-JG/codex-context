from __future__ import annotations

from .models import ProjectFacts


def agents_body(facts: ProjectFacts) -> str:
    lines: list[str] = []
    if facts.purpose:
        lines.append(facts.purpose)
    if facts.languages:
        stack = facts.languages + facts.frameworks
        lines.append("Stack: " + ", ".join(stack) + ".")
    short = [f"{category}: `{command}`" for category, command in facts.commands.items()
             if category in {"test", "lint", "typecheck", "validate"} and len(command) <= 48]
    if short:
        lines.append("Validate: " + "; ".join(short[:3]) + ".")
    if not lines:
        lines.append("Project context not yet detected; add durable project constraints when established.")
    return "\n".join(lines)


def memories(facts: ProjectFacts) -> dict[str, str]:
    notes = dict(facts.durable_notes)
    if facts.commands:
        lines = ["# Commands", "", "Run from the repository root with project dependencies installed."]
        lines.extend(f"- {category}: `{command}`" for category, command in facts.commands.items())
        if "uv" in facts.tools:
            lines.append("Commands use the uv-managed project environment.")
        notes["commands"] = "\n".join(lines) + "\n"
    if notes:
        pointers = ["# Project", ""]
        labels = {"commands": "Full validation and environment prerequisites", "architecture": "Architectural invariants",
                  "decisions": "Architectural rationale", "pitfalls": "Known project pitfalls",
                  "browser-architecture": "Documented browser lifecycle",
                  "resources-performance": "Documented resource constraints"}
        pointers.extend(f"- {labels[name]}: `mem:{name}`." for name in sorted(notes))
        notes["project"] = "\n".join(pointers) + "\n"
    # No placeholder files, invented decisions or generic framework guidance.
    return notes
