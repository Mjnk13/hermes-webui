"""Workspace git diff API coverage for Changed This Turn diff baseline UX."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
ROUTES_PY = (REPO / "api" / "routes.py").read_text(encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture()
def git_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Hermes Test")
    tracked = repo / "tracked.txt"
    tracked.write_text("old\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")

    import api.workspace as workspace

    monkeypatch.setattr(workspace, "load_workspaces", lambda: [{"path": str(repo)}])
    return repo


def test_workspace_git_diff_returns_explicit_head_baseline_for_tracked_file(git_workspace: Path):
    from api.rollback import get_workspace_git_diff

    (git_workspace / "tracked.txt").write_text("new\n", encoding="utf-8")

    payload = get_workspace_git_diff(str(git_workspace), "tracked.txt")

    assert payload["ok"] is True
    assert payload["path"] == "tracked.txt"
    assert payload["repo_path"] == "tracked.txt"
    assert payload["baseline"] == "git:HEAD"
    assert payload["baseline_label"] == "Current workspace git diff vs HEAD"
    assert payload["has_diff"] is True
    assert "-old" in payload["diff"]
    assert "+new" in payload["diff"]


def test_workspace_git_diff_includes_untracked_files(git_workspace: Path):
    from api.rollback import get_workspace_git_diff

    (git_workspace / "created.txt").write_text("hello\n", encoding="utf-8")

    payload = get_workspace_git_diff(str(git_workspace), "created.txt")

    assert payload["status"] in {"??", "untracked"}
    assert payload["has_diff"] is True
    assert "/dev/null" in payload["diff"]
    assert "+hello" in payload["diff"]


def test_workspace_git_diff_blocks_paths_outside_workspace(git_workspace: Path, tmp_path: Path):
    from api.rollback import get_workspace_git_diff

    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Path traversal blocked"):
        get_workspace_git_diff(str(git_workspace), str(outside))

    with pytest.raises(ValueError, match="Path traversal blocked"):
        get_workspace_git_diff(str(git_workspace), "../outside.txt")


def test_workspace_git_diff_route_is_exposed_with_required_query_params():
    assert 'parsed.path == "/api/workspace/git-diff"' in ROUTES_PY
    assert '"workspace and path query parameters are required"' in ROUTES_PY
    assert "get_workspace_git_diff(workspace, path)" in ROUTES_PY
