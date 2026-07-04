"""TDD for ``changed_paths``: the files a single commit changed (via git diff-tree).

Hermetic inline git repo (no Neo4j). Proves the diff-tree contract that MODIFIES
adoption depends on:

* a ROOT commit (no parent) reports its WHOLE tree (``--root``) — else the initial
  import silently drops every file it introduced;
* a normal commit reports ONLY the paths it actually changed (ac-1 grounding);
* a malformed diff-tree stream fails loud rather than mis-pairing status/path.
"""

import subprocess
from pathlib import Path

import pytest

from palimpsest.extract.provenance import changed_paths


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    """A 3-commit repo: root adds two files, c2 edits one, c3 deletes one."""
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "a.txt").write_text("a1\n")
    (r / "b.txt").write_text("b1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "root")
    (r / "a.txt").write_text("a2\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "edit a")
    (r / "b.txt").unlink()
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "delete b")
    return r


def _shas(repo) -> list[str]:
    return [s for s in _git(repo, "log", "--format=%H", "--reverse").splitlines() if s]


def test_root_commit_reports_whole_tree(repo):
    root = _shas(repo)[0]
    assert set(changed_paths(repo, root)) == {"a.txt", "b.txt"}


def test_normal_commit_reports_only_changed_paths(repo):
    c2 = _shas(repo)[1]
    assert changed_paths(repo, c2) == ["a.txt"]


def test_delete_commit_reports_the_deleted_path(repo):
    c3 = _shas(repo)[2]
    assert changed_paths(repo, c3) == ["b.txt"]
