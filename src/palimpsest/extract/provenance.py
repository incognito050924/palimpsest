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
