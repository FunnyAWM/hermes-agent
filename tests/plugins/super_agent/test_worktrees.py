"""Real Git worktree lifecycle for Super Agent assignments."""

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from plugins.super_agent import _command
from plugins.super_agent.runtime import SuperAgentRuntime
from plugins.super_agent.store import SuperAgentStore


def _git(*args, cwd=None):
    return subprocess.run(
        ["git", *map(str, args)], cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git("init", path)
    _git("-C", path, "config", "user.email", "super-agent@example.test")
    _git("-C", path, "config", "user.name", "Super Agent Test")
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git("-C", path, "add", "tracked.txt")
    _git("-C", path, "commit", "-m", "base")
    return path.resolve()


def _assignment(store, root, event_id):
    store.submit(
        task="task", sandbox="workspace-write", workspace_root=str(root),
        source_instance="test", external_event_id=event_id, source=None,
    )
    return store.claim_next()


@pytest.mark.asyncio
async def test_assignments_get_distinct_reusable_worktrees_with_safe_paths(tmp_path):
    root = _repo(tmp_path / "仓库 with spaces")
    store = SuperAgentStore(tmp_path / "data", "default")
    runtime = SuperAgentRuntime(SimpleNamespace(plugin_id="super_agent"), store)
    first = _assignment(store, root, "first")
    second = _assignment(store, root, "second")

    first_path = await runtime._ensure_worktree(first)
    second_path = await runtime._ensure_worktree(second)

    assert first_path != second_path
    assert Path(_git("-C", first_path, "rev-parse", "--show-toplevel")).resolve() == first_path
    assert Path(_git("-C", second_path, "rev-parse", "--show-toplevel")).resolve() == second_path
    assert await runtime._ensure_worktree(first) == first_path
    assert first_path.parent == second_path.parent
    assert first_path.is_relative_to(root)


@pytest.mark.asyncio
async def test_parent_ignores_nested_repositories_but_keeps_its_own_changes(tmp_path):
    root = _repo(tmp_path / "repo")
    child = _repo(root / "projects" / "nested repo")
    (child / "tracked.txt").write_text("child edit", encoding="utf-8")
    store = SuperAgentStore(tmp_path / "data", "default")
    runtime = SuperAgentRuntime(SimpleNamespace(plugin_id="super_agent"), store)
    assignment = _assignment(store, root, "parent")
    loose = child.parent / "parent file.txt"
    loose.touch()
    with pytest.raises(RuntimeError, match="dirty"):
        await runtime._ensure_worktree(assignment)
    loose.unlink()
    assert (await runtime._ensure_worktree(assignment) / "tracked.txt").read_text() == "base\n"

    _git("-C", root, "add", "--", child)
    with pytest.raises(RuntimeError, match="dirty"):
        await runtime._ensure_worktree(_assignment(store, root, "staged-gitlink"))
    _git("-C", root, "commit", "-m", "pin child")
    _git("-C", child, "commit", "-am", "advance child")
    assert (await runtime._ensure_worktree(_assignment(store, root, "pinned"))).is_dir()
    _git("-C", root, "add", "--", child)
    with pytest.raises(RuntimeError, match="dirty"):
        await runtime._ensure_worktree(_assignment(store, root, "changed-pin"))


@pytest.mark.asyncio
async def test_named_project_uses_its_own_head_and_local_worktree(tmp_path, monkeypatch):
    root = _repo(tmp_path / "Playground")
    child = _repo(root / "kourichat-next")
    (child / "android.txt").touch()
    _git("-C", child, "add", "android.txt")
    _git("-C", child, "commit", "-m", "android")
    (root / "parent-wip").touch()
    store = SuperAgentStore(tmp_path / "data", "default")
    ctx = SimpleNamespace(plugin_id="super_agent", get_config={"workspace_root": str(root)}.get)
    runtime = SuperAgentRuntime(ctx, store)
    monkeypatch.setattr(runtime, "wake", lambda: None)
    await _command(ctx, runtime, "new 请检查kourichat-next的构建")
    assignment = store.claim_next()
    workspace = await runtime._ensure_worktree(assignment)
    assert workspace != child and workspace.is_relative_to(child)
    assert (workspace / "android.txt").exists()
    assert _git("-C", workspace, "rev-parse", "HEAD") == _git("-C", child, "rev-parse", "HEAD")
    assert (root / "parent-wip").exists()

    other = _repo(root / "projects" / "other")
    reply = await _command(ctx, runtime, "new compare kourichat-next and projects/other")
    assert "kourichat-next" in reply and "projects/other" in reply
    assert store.claim_next() is None
    await _command(ctx, runtime, r"new inspect projects\other")
    assert Path(store.claim_next()["workspace_root"]) == other


@pytest.mark.asyncio
async def test_non_git_and_existing_foreign_paths_fail_without_deleting_files(tmp_path):
    store = SuperAgentStore(tmp_path / "data", "default")
    runtime = SuperAgentRuntime(SimpleNamespace(plugin_id="super_agent"), store)
    non_git = tmp_path / "not-a-repo"
    non_git.mkdir()
    assignment = _assignment(store, non_git, "non-git")

    with pytest.raises(RuntimeError, match="git .*rev-parse"):
        await runtime._ensure_worktree(assignment)

    root = _repo(tmp_path / "repo")
    foreign = _assignment(store, root, "foreign")
    foreign_path = Path(foreign["workspace"])
    foreign_path.mkdir(parents=True)
    marker = foreign_path / "user-file.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(RuntimeError):
        await runtime._ensure_worktree(foreign)
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_dirty_main_checkout_is_rejected_before_worktree_creation(tmp_path):
    root = _repo(tmp_path / "repo")
    (root / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    store = SuperAgentStore(tmp_path / "data", "default")
    runtime = SuperAgentRuntime(SimpleNamespace(plugin_id="super_agent"), store)
    assignment = _assignment(store, root, "dirty")

    with pytest.raises(RuntimeError, match="dirty"):
        await runtime._ensure_worktree(assignment)
    assert not Path(assignment["workspace"]).exists()


@pytest.mark.asyncio
async def test_git_timeout_kills_and_reaps_child(monkeypatch):
    class Process:
        returncode = None
        killed = False
        reaped = False

        async def communicate(self):
            return b"", b""

        def kill(self):
            self.killed = True

        async def wait(self):
            self.reaped = True

    process = Process()

    async def create_subprocess(*_args, **_kwargs):
        return process

    async def timeout(awaitable, *, timeout):
        if timeout == 60:
            awaitable.close()
            raise TimeoutError
        assert timeout == 5
        return await awaitable

    monkeypatch.setattr("plugins.super_agent.runtime.asyncio.create_subprocess_exec", create_subprocess)
    monkeypatch.setattr("plugins.super_agent.runtime.asyncio.wait_for", timeout)

    with pytest.raises(RuntimeError, match="git status timed out"):
        await SuperAgentRuntime._git("status")
    assert process.killed is True and process.reaped is True


@pytest.mark.platforms("windows")
@pytest.mark.asyncio
async def test_windows_non_ascii_worktree_path_round_trips_through_git(tmp_path):
    root = _repo(tmp_path / "Windows 仓库")
    store = SuperAgentStore(tmp_path / "数据 with spaces", "default")
    runtime = SuperAgentRuntime(SimpleNamespace(plugin_id="super_agent"), store)
    assignment = _assignment(store, root, "windows")

    workspace = await runtime._ensure_worktree(assignment)

    assert os.path.normcase(_git("-C", workspace, "rev-parse", "--show-toplevel")) == os.path.normcase(str(workspace))
