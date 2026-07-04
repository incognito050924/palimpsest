"""Read git provenance for a pinned commit, once, via ``git``."""

from __future__ import annotations

import subprocess
from pathlib import Path

from palimpsest.ir import Provenance


def read_provenance(repo_path: Path | str, commit: str = "HEAD") -> Provenance:
    """Read source_commit / author / committed_at for ``commit`` in ``repo_path``.

    Uses one ``git show`` so the three fields come from a single pinned SHA.
    """

    repo_path = str(repo_path)

    def git(*args: str) -> str:
        out = subprocess.run(
            ["git", "-C", repo_path, *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    # %H = full sha, %an/%ae = author name/email, %cI = committer date (ISO-8601 strict)
    line = git("show", "-s", "--format=%H%x1f%an <%ae>%x1f%cI", commit)
    sha, author, committed_at = line.split("\x1f")
    return Provenance(source_commit=sha, author=author, committed_at=committed_at)


def changed_paths(repo_path: Path | str, commit: str = "HEAD") -> list[str]:
    """The repo-relative paths ``commit`` changed, once, via ``git diff-tree``.

    Underpins the MODIFIES edge (Episode -> File): each commit binds only to the
    files it actually touched. Flags, each load-bearing:

    * ``--root`` — a parent-less ROOT commit is diffed against the empty tree, so
      the initial import reports its whole tree instead of nothing (silent
      under-capture otherwise).
    * ``--first-parent`` — under this flag a merge commit emits an *empty*
      diff-tree (git yields no per-file rows for a merge), so a merge Episode
      contributes **zero** MODIFIES edges. This is intended, not a defect: the
      individual changes a merge combined are already bound to their own commits
      via MODIFIES, so re-binding them to the merge Episode would double-count
      churn/co-change. Accepted gap: an evil-merge (content unique to the merge,
      present in neither parent) binds to no Episode — a silent omission we accept
      because churn/co-change are additive signals. On linear (non-merge) history
      the flag is a no-op, so it stays.
    * ``--no-renames`` — a rename is reported as delete-old + add-new (two plain
      paths) instead of a rename record, keeping every record a single path.
    * ``-z`` — NUL-delimited records (paths with spaces/newlines are safe).
    * ``--no-commit-id`` — suppress the leading commit-id line so the stream is
      pure ``status\\0path\\0`` pairs.

    Records arrive as ``status\\0path`` pairs; an odd token count means the stream
    did not pair up (off-contract) and we fail loud rather than mis-align paths
    (mirrors the split-unpack precedent in :func:`read_provenance`). A deleted
    path is returned like any other changed path — the MODIFIES writer MATCHes
    (never MERGEs) the File node, so a path with no HEAD File node simply gets no
    edge, never a phantom File.
    """

    out = subprocess.run(
        ["git", "-C", str(repo_path), "diff-tree", "--root", "--no-commit-id",
         "--no-renames", "--first-parent", "-r", "-z", "--name-status", commit],
        check=True, capture_output=True, text=True,
    ).stdout
    tokens = [t for t in out.split("\x00") if t]
    if len(tokens) % 2 != 0:
        raise ValueError(
            f"git diff-tree emitted an unpaired status/path stream for {commit!r}: "
            f"{len(tokens)} tokens"
        )
    return [tokens[i + 1] for i in range(0, len(tokens), 2)]
