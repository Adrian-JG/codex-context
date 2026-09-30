import pytest
from conftest import snapshot

from codex_context.artifacts import END, START, ChangePlan, block_merge, generated_text
from codex_context.cli import main
from codex_context.config import load_settings
from codex_context.detection import detect
from codex_context.models import Conflict, Ownership, Settings
from codex_context.project import init_project, update_map
from codex_context.serena import Serena


def test_empty_init_without_serena(repo):
    messages = init_project(repo)
    assert (repo / "AGENTS.md").exists()
    assert (repo / ".ai/context.yml").exists()
    assert (repo / ".ai/repo-map.md").exists()
    assert not (repo / ".serena/project.yml").exists()
    assert not list(repo.glob(".serena/memories/*.md"))
    assert "Serena: not installed" in messages
    assert not (repo / ".ai/tools").exists()


def test_python_init_is_lazy(python_repo):
    init_project(python_repo)
    files = sorted(p.name for p in (python_repo / ".serena/memories").glob("*.md"))
    assert files == ["commands.md", "project.md"]
    assert "python -m pytest" in (python_repo / "AGENTS.md").read_text()
    assert "mem:commands" in (python_repo / ".serena/memories/project.md").read_text()
    settings = load_settings(python_repo)
    assert settings.extractors == "auto" and settings.tracked and settings.budget_tokens == 1000


def test_python_playwright_no_invented_memories(python_repo):
    with (python_repo / "pyproject.toml").open("a") as stream:
        stream.write('\n[project.optional-dependencies]\nbrowser = ["playwright"]\n')
    facts = detect(python_repo, Settings())
    assert facts.profile == "python-playwright"
    init_project(python_repo)
    assert not (python_repo / ".serena/memories/browser-architecture.md").exists()
    assert not (python_repo / ".serena/memories/resources-performance.md").exists()


def test_durable_memories_only_with_evidence(python_repo):
    (python_repo / "README.md").write_text("## Architectural decisions\n\nThe service owns lifecycle transitions so adapters cannot mutate state.\n", encoding="utf-8")
    init_project(python_repo)
    text = (python_repo / ".serena/memories/decisions.md").read_text(encoding="utf-8")
    assert "README.md" in text
    assert "adapters cannot mutate" not in text  # reference, not duplicated documentation
    assert not (python_repo / ".serena/memories/pitfalls.md").exists()


def test_agents_manual_content_preserved(python_repo):
    path = python_repo / "AGENTS.md"
    before = b"\xef\xbb\xbfManual instructions.\r\n\r\nKeep this.\r\n"
    path.write_bytes(before)
    init_project(python_repo)
    assert path.read_bytes().startswith(before)
    with path.open("ab") as stream:
        stream.write(b"\r\nManual tail.\r\n")
    init_project(python_repo, force=True)
    assert path.read_bytes().endswith(b"\r\nManual tail.\r\n")
    assert path.read_text(encoding="utf-8-sig").count(START) == 1


def test_repeated_init_preserves_bytes_and_mtimes(python_repo):
    init_project(python_repo)
    before = snapshot(python_repo)
    init_project(python_repo)
    assert snapshot(python_repo) == before
    init_project(python_repo, force=True)
    assert snapshot(python_repo) == before


def test_dry_run_writes_nothing(python_repo):
    before = snapshot(python_repo)
    init_project(python_repo, dry_run=True)
    assert snapshot(python_repo) == before
    assert not (python_repo / ".ai").exists()
    init_project(python_repo)
    (python_repo / "new.py").write_text("def new(): pass\n")
    before = snapshot(python_repo)
    update_map(python_repo, refresh=True, dry_run=True)
    assert snapshot(python_repo) == before


@pytest.mark.parametrize("force", [False, True])
def test_memories_remain_user_owned(python_repo, force):
    directory = python_repo / ".serena/memories"
    directory.mkdir(parents=True)
    memory = directory / "commands.md"
    memory.write_bytes(b"User-established commands.\r\n")
    init_project(python_repo, force=force)
    assert memory.read_bytes() == b"User-established commands.\r\n"
    memory.write_text("Changed by agent after init.\n")
    init_project(python_repo, force=force)
    update_map(python_repo, refresh=True, force=force)
    assert memory.read_text() == "Changed by agent after init.\n"


@pytest.mark.parametrize("force", [False, True])
def test_force_cannot_claim_existing_or_modified_map(repo, force):
    directory = repo / ".ai"
    directory.mkdir()
    path = directory / "repo-map.md"
    path.write_text("Manual map\n")
    before = snapshot(repo)
    with pytest.raises(Conflict):
        init_project(repo, force=force)
    assert snapshot(repo) == before
    path.write_text(generated_text("Generated map\n") + "Manual addition\n")
    with pytest.raises(Conflict):
        init_project(repo, force=force)


def test_preexisting_cache_is_not_claimed(repo):
    path = repo / ".ai/.cache/structure.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"manual": "keep"}\n')
    before = snapshot(repo)
    with pytest.raises(Conflict):
        init_project(repo, force=True)
    assert snapshot(repo) == before


def test_context_preserves_comments_custom_values_and_unknown_keys(python_repo):
    folder = python_repo / ".ai"
    folder.mkdir()
    path = folder / "context.yml"
    path.write_text("# Keep my comment\nrepo_map:\n  budget_tokens: 200\n  tracked: false\nignored_paths: ['private/**']\ncustom: yes\n", encoding="utf-8")
    init_project(python_repo)
    text = path.read_text()
    assert "# Keep my comment" in text and "custom: yes" in text and "budget_tokens: 200" in text
    assert "/repo-map.md" in (folder / ".gitignore").read_text()
    assert "/.cache/" in (folder / ".gitignore").read_text()
    assert list(folder.glob("*.json")) == []
    before = snapshot(python_repo)
    init_project(python_repo)
    assert before == snapshot(python_repo)


def test_managed_block_conflicts():
    for text in (START, END + START, START + END + START + END):
        with pytest.raises(Conflict):
            block_merge(text, "replacement")


def test_transaction_restores_on_write_failure(repo, monkeypatch):
    from codex_context import artifacts
    path = repo / "one.md"
    path.write_text("manual")
    plan = ChangePlan(repo)
    plan.add(path, "managed", Ownership.STRUCTURED_MERGE)
    plan.add(repo / "new/two.md", "new", Ownership.CREATE_ONCE_USER_OWNED)
    real_write = artifacts.atomic_write

    def fail_second(target, text):
        if target.name == "two.md":
            raise OSError("simulated failure")
        real_write(target, text)

    monkeypatch.setattr(artifacts, "atomic_write", fail_second)
    with pytest.raises(OSError):
        plan.apply()
    assert path.read_text() == "manual"
    assert not (repo / "new").exists()


def test_artifact_parent_symlink_escape(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (repo / ".ai").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(Conflict):
        init_project(repo)
    assert not list(outside.iterdir())


def test_serena_native_config_merge_preserves_user_values(python_repo, monkeypatch):
    from codex_context.config import yaml_load
    template = yaml_load('# servers: python typescript\nproject_name: probe\nlanguage_servers: [python]\ninitial_prompt: ""\nignore_all_files_in_gitignore: true\nignored_paths: []\n', "test native schema")
    monkeypatch.setattr(Serena, "detect", classmethod(lambda cls: Serena("serena", "1.7.0", True)))
    monkeypatch.setattr(Serena, "native_template", lambda self: template.copy())
    path = python_repo / ".serena/project.yml"
    path.parent.mkdir()
    path.write_text('# User comment\nproject_name: custom-name\nlanguage_servers: [typescript]\ninitial_prompt: "Manual prompt"\nignore_all_files_in_gitignore: false\ncustom: keep\n')
    init_project(python_repo)
    data = yaml_load(path.read_text(), "result")
    assert data["language_servers"] == ["typescript", "python"]
    assert data["project_name"] == "custom-name" and data["initial_prompt"] == "Manual prompt"
    assert data["ignore_all_files_in_gitignore"] is False and "# User comment" in path.read_text()


def test_cli_return_codes(repo, capsys):
    assert main(["init", str(repo)]) == 0
    assert main(["doctor", str(repo)]) == 3
    assert "repo map: fresh" in capsys.readouterr().out
    assert main(["init", str(repo / "absent")]) == 1
    (repo / ".ai/context.yml").write_text("repo_map: {budget_tokens: invalid}\n")
    assert main(["map", str(repo)]) == 2


def test_node_rust_go_detection(repo):
    (repo / "package.json").write_text('{"scripts":{"test":"vitest"},"dependencies":{"react":"*"}}')
    (repo / "Cargo.toml").write_text('[package]\nname="rust_example"\nversion="0.1.0"\n')
    (repo / "go.mod").write_text("module example\n")
    facts = detect(repo, Settings())
    assert set(facts.languages) == {"typescript", "rust", "go"}
    assert set(facts.tools) >= {"npm", "cargo", "go"}
    assert "React" in facts.frameworks
