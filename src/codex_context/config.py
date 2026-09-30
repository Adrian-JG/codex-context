from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .models import ConfigError, Settings

PROFILES = {"auto", "base", "python", "python-playwright", "node"}
EXTRACTORS = {"python", "generic"}


def yaml_load(text: str, label: str) -> CommentedMap:
    try:
        data = YAML().load(text)
    except Exception as exc:
        raise ConfigError(f"{label}: invalid YAML: {exc}") from exc
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap):
        raise ConfigError(f"{label}: expected a YAML mapping")
    return data


def yaml_dump(data: Any) -> str:
    stream = StringIO()
    yaml = YAML()
    yaml.width = 110
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.dump(data, stream)
    return stream.getvalue()


def default_config() -> CommentedMap:
    return CommentedMap({
        "schema_version": 1,
        "repo_map": CommentedMap({"budget_tokens": 1000, "tracked": True}),
        "ignored_paths": [], "profile": "auto", "extractors": "auto",
    })


def merge_defaults(text: str) -> str:
    data = yaml_load(text, ".ai/context.yml")
    for key, value in default_config().items():
        if key not in data:
            data[key] = value
        elif key == "repo_map":
            if not isinstance(data[key], dict):
                raise ConfigError("repo_map must be a mapping")
            for nested, default in value.items():
                data[key].setdefault(nested, default)
    settings_from_data(data)
    # Preserve an already complete configuration byte for byte.
    original = yaml_load(text, ".ai/context.yml")
    return text if original == data else yaml_dump(data)


def settings_from_data(data: dict[str, Any]) -> Settings:
    version = data.get("schema_version", 1)
    if type(version) is not int or version != 1:
        raise ConfigError("schema_version must be 1")
    mapping = data.get("repo_map", {})
    if not isinstance(mapping, dict):
        raise ConfigError("repo_map must be a mapping")
    budget = mapping.get("budget_tokens", 1000)
    tracked = mapping.get("tracked", True)
    if type(budget) is not int or budget < 64:
        raise ConfigError("repo_map.budget_tokens must be an integer >= 64")
    if type(tracked) is not bool:
        raise ConfigError("repo_map.tracked must be true or false")
    ignored = data.get("ignored_paths", [])
    if not isinstance(ignored, list) or not all(isinstance(p, str) for p in ignored):
        raise ConfigError("ignored_paths must be a list of patterns")
    profile = data.get("profile", "auto")
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ConfigError(f"unknown profile: {profile}")
    extractors = data.get("extractors", "auto")
    if extractors != "auto":
        if not isinstance(extractors, list) or not extractors or not all(
            isinstance(e, str) and e in EXTRACTORS for e in extractors
        ) or len(set(extractors)) != len(extractors):
            raise ConfigError("extractors must be auto or a unique list of registered extractors")
    return Settings(budget, tracked, list(ignored), profile, extractors)


def load_settings(root: Path) -> Settings:
    path = root / ".ai/context.yml"
    if not path.exists():
        return Settings()
    return settings_from_data(yaml_load(path.read_text(encoding="utf-8-sig"), str(path)))
