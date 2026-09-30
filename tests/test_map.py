import math
import subprocess

import pytest

from codex_context.config import load_settings
from codex_context.extractors import PythonExtractor, extract_file
from codex_context.files import discover_files
from codex_context.models import ContextError, Settings
from codex_context.project import init_project, update_map
from codex_context.repo_map import build_map, estimated_tokens, internal_edges, map_status


def test_estimator_uses_four_utf8_bytes():
    text = "a" * 4000
    assert estimated_tokens(text) == 1000
    assert estimated_tokens("á🙂") == math.ceil(len("á🙂".encode()) / 4)


def test_python_ast_signatures_and_no_bodies(python_repo):
    source = python_repo / "src/example/core.py"
    result = PythonExtractor().extract(source, python_repo)
    text = " ".join(s.text for s in result.symbols)
    assert "class Widget(Base)" in text
    assert "amount: int=1" in text and "async load(name: str, *, retries: int=3) -> Widget" in text
    assert "return" not in text and "docstring" not in text and "_private" not in text
    consumer = PythonExtractor().extract(python_repo / "src/example/consumer.py", python_repo)
    assert internal_edges([result, consumer])[consumer.path] == {result.path}


def test_auto_specific_claims_file_once(python_repo):
    path = python_repo / "src/example/core.py"
    result = extract_file(path, python_repo, "auto")
    assert result.language == "python" and result.symbols
    result = extract_file(path, python_repo, ["generic", "python"])
    assert result.language == "python"
    init_project(python_repo)
    text = (python_repo / ".ai/repo-map.md").read_text(encoding="utf-8")
    assert text.count("`src/example/core.py`") == 1


def test_map_deterministic_and_budget(repo):
    for index in range(100):
        (repo / f"module_{index:03}.py").write_text(f"class Public{index}:\n    def method(self, value: str) -> str: return value\n")
    settings = Settings(budget_tokens=200)
    first = build_map(repo, settings)
    assert first.content == build_map(repo, settings).content
    assert estimated_tokens(first.content) <= 200
    assert "omitted" in first.content


def test_import_centrality_selects_central_module(repo):
    (repo / "aaa_peripheral.py").write_text("class Peripheral: pass\n")
    (repo / "zzz_central.py").write_text("class Central: pass\n")
    for index in range(10):
        (repo / f"user_{index}.py").write_text("from zzz_central import Central\n")
    result = build_map(repo, Settings(budget_tokens=95))
    assert "zzz_central.py" in result.content
    assert estimated_tokens(result.content) <= 95


def test_discovery_git_tracked_untracked_and_ignored(repo):
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    (repo / "tracked.py").write_text("class Tracked: pass\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.py"], check=True)
    (repo / "new.py").write_text("class New: pass\n")
    (repo / "ignored.py").write_text("class Ignored: pass\n")
    (repo / ".gitignore").write_text("ignored.py\ntracked.py\n.ai/.cache/\n")
    files = {p.name for p in discover_files(repo)}
    assert "new.py" in files and "ignored.py" not in files and "tracked.py" not in files
    init_project(repo)
    ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", ".ai/.cache/structure.json"], capture_output=True)
    assert ignored.returncode == 0


def test_filesystem_fallback_nested_ignores_and_binaries(repo):
    (repo / ".gitignore").write_text("ignored.py\n")
    (repo / "ignored.py").write_text("ignored")
    (repo / "binary.dat").write_bytes(b"\0data")
    (repo / "node_modules").mkdir()
    (repo / "node_modules/a.py").write_text("ignored")
    nested = repo / "nested"
    nested.mkdir()
    (nested / ".gitignore").write_text("*.py\n!keep.py\n")
    (nested / "omit.py").write_text("ignored")
    (nested / "keep.py").write_text("class Keep: pass\n")
    paths = {p.relative_to(repo).as_posix() for p in discover_files(repo)}
    assert "nested/keep.py" in paths
    assert not {"ignored.py", "binary.dat", "node_modules/a.py", "nested/omit.py"} & paths
    assert "nested/keep.py" not in {p.relative_to(repo).as_posix() for p in discover_files(repo, ["nested/**"])}


def test_staleness_body_vs_signature(python_repo):
    init_project(python_repo)
    settings = load_settings(python_repo)
    source = python_repo / "src/example/core.py"
    before = (python_repo / ".ai/repo-map.md").stat().st_mtime_ns
    source.write_text(source.read_text().replace("amount + 1", "amount + 2").replace("omitted docstring", "changed docstring"))
    result = build_map(python_repo, settings)
    assert map_status(python_repo, result)[0] == "fresh"
    update_map(python_repo, refresh=True)
    assert (python_repo / ".ai/repo-map.md").stat().st_mtime_ns == before
    source.write_text(source.read_text().replace("amount: int = 1", "amount: int = 10"))
    assert map_status(python_repo, build_map(python_repo, settings))[0] == "stale"
    update_map(python_repo, refresh=True)
    assert map_status(python_repo, build_map(python_repo, settings))[0] == "fresh"


@pytest.mark.parametrize("change", ["new", "deleted", "import", "class", "config"])
def test_structural_changes_invalidate(python_repo, change):
    init_project(python_repo)
    source = python_repo / "src/example/core.py"
    if change == "new":
        (python_repo / "new.py").write_text("class New: pass\n")
    elif change == "deleted":
        source.unlink()
    elif change == "import":
        source.write_text("import os\n" + source.read_text())
    elif change == "class":
        source.write_text(source.read_text().replace("class Widget(Base)", "class Widget"))
    else:
        path = python_repo / ".ai/context.yml"
        path.write_text(path.read_text().replace("1000", "200"))
    assert map_status(python_repo, build_map(python_repo, load_settings(python_repo)))[0] == "stale"


def test_changed_extractors_invalidate_and_do_not_reuse_wrong_cache(python_repo):
    init_project(python_repo)
    settings = load_settings(python_repo)
    settings.extractors = ["generic"]
    result = build_map(python_repo, settings)
    assert result.changed_structure
    assert "class Widget" not in result.content


def test_parse_failure_preserves_existing_map(python_repo):
    init_project(python_repo)
    path = python_repo / ".ai/repo-map.md"
    before = path.read_bytes()
    (python_repo / "broken.py").write_text("def broken(:\n")
    with pytest.raises(ContextError, match="incomplete"):
        update_map(python_repo, refresh=True)
    assert path.read_bytes() == before
    result = build_map(python_repo, load_settings(python_repo))
    assert map_status(python_repo, result)[0] == "stale"


def test_clone_map_without_cache_requires_refresh(python_repo):
    init_project(python_repo)
    (python_repo / ".ai/.cache/structure.json").unlink()
    assert map_status(python_repo, build_map(python_repo, load_settings(python_repo)))[1].startswith("local")
    update_map(python_repo, refresh=True)
    assert map_status(python_repo, build_map(python_repo, load_settings(python_repo)))[0] == "fresh"
