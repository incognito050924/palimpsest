"""N-way branch capture: project several branches' full histories into the KG.

Backfill (:mod:`palimpsest.backfill`) replays extract -> ingest over ONE line of
history into the bare-id plane. Reconcile generalizes that to N branches so
divergent versions of the SAME symbol coexist as distinct, comparable nodes
(ac-1) without collapsing — each branch gets its own identity namespace
(``scope_to_branch``) folded into the MERGE key.

Design invariants (provider-free — no LLM, no network beyond git + Neo4j):

  * Dedup the EXTRACT axis, never the HISTORY axis. The UNION of commits is
    enumerated once (``git rev-list --reverse --date-order``) and each unique tree
    is materialized + extracted ONCE; the resulting IR is then scoped + ingested
    once per branch that contains the commit (membership fan-out). Full per-branch
    history is preserved (every reachable commit lands as an Episode) — merge-base
    truncation is rejected (ac-5).
  * scoped-rebuild (delete-then-project): each specified branch's plane is wiped
    ONCE up front (``wipe_branch_plane``) so shrink/rebase/tip-move leave no stale
    nodes. The bare-id plane is never touched (ac-6).
  * Partial-capture honesty: a ``CaptureManifest`` per branch flips to
    ``captured`` only AFTER all its commits ingest — a mid-run failure leaves it
    ``pending`` (never silently captured).
  * Fail-closed: a shallow repository is refused before any extract (a truncated
    graph is never persisted). Branch args are git-safe (validated, never parsed
    as git options) and deduped.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from palimpsest.backfill import _materialize_tree
from palimpsest.extract import extract, read_provenance
from palimpsest.ir import scope_to_branch
from palimpsest.kg import augment_communities, ingest
from palimpsest.kg.ingest import (
    CAPTURE_MANIFEST,
    create_constraints,
    wipe_branch_plane,
)

# Unit Separator — joins the capture key (sorted branch names) to a branch in a
# CaptureManifest id; mirrors the branch-scoped id separator in ir.py.
_US = "\x1f"


@dataclass(frozen=True)
class CaptureResult:
    """What an N-way capture projected.

    ``branches`` is the deduped, sorted, validated branch set actually captured.
    ``commits`` is the size of the UNION of their histories (unique commits, each
    materialized + extracted once).
    """

    branches: tuple[str, ...]
    commits: int


def _git(repo_path: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_path, *args],
        check=False, capture_output=True, text=True,
    )


def _is_shallow(repo_path: str) -> bool:
    """True iff the repo is a shallow clone (truncated history)."""
    out = _git(repo_path, "rev-parse", "--is-shallow-repository")
    if out.returncode == 0:
        val = out.stdout.strip().lower()
        if val in ("true", "false"):
            return val == "true"
    # Fallback for git versions without the flag: the shallow marker file.
    marker = _git(repo_path, "rev-parse", "--git-path", "shallow")
    if marker.returncode == 0:
        p = Path(marker.stdout.strip())
        if not p.is_absolute():
            p = Path(repo_path) / p
        return p.exists()
    return False


def _validate_branches(repo_path: str, branches) -> list[str]:
    """Dedup + sort + validate. Git-safe: each ref is peeled to a commit AFTER
    ``--end-of-options`` so a name like ``-x`` can't be parsed as a git option.
    A nonexistent branch is rejected honestly (ValueError), not as a raw
    CalledProcessError."""
    if not branches:
        raise ValueError("capture requires at least one branch")
    uniq = sorted(set(branches))
    for b in uniq:
        out = _git(
            repo_path, "rev-parse", "--verify", "--quiet",
            "--end-of-options", f"{b}^{{commit}}",
        )
        if out.returncode != 0:
            raise ValueError(f"unknown or invalid branch: {b!r}")
    return uniq


def _head_sha(repo_path: str, branch: str) -> str:
    """The branch tip commit. Uses the git-safe ``--verify`` peel form (which
    prints only the object name — a bare ``--end-of-options`` would be echoed)."""
    out = _git(
        repo_path, "rev-parse", "--verify", "--end-of-options",
        f"{branch}^{{commit}}",
    )
    out.check_returncode()
    return out.stdout.strip()


def _rev_list_union(repo_path: str, branches: list[str]) -> list[str]:
    """The UNION of commits reachable from any branch, each once, oldest-first."""
    out = _git(
        repo_path, "rev-list", "--reverse", "--date-order",
        "--end-of-options", *branches,
    )
    out.check_returncode()
    return [x for x in out.stdout.splitlines() if x]


def _membership(repo_path: str, branches: list[str]) -> dict[str, set[str]]:
    """commit SHA -> set of branches that contain it (invert per-branch rev-list)."""
    membership: dict[str, set[str]] = {}
    for b in branches:
        out = _git(repo_path, "rev-list", "--end-of-options", b)
        out.check_returncode()
        for sha in out.stdout.splitlines():
            if sha:
                membership.setdefault(sha, set()).add(b)
    return membership


_MANIFEST_MERGE = """
MERGE (m:CaptureManifest {id: $id})
SET m.capture_key = $capture_key,
    m.branch       = $branch,
    m.status       = $status,
    m.commit_count = $commit_count,
    m.head_sha     = $head_sha,
    m.captured_at  = $captured_at
"""


def _write_manifest(driver, capture_key, branch, *, status, commit_count,
                    head_sha, captured_at) -> None:
    mid = f"capture:{capture_key}{_US}{branch}"
    with driver.session() as session:
        session.run(
            _MANIFEST_MERGE, id=mid, capture_key=capture_key, branch=branch,
            status=status, commit_count=commit_count, head_sha=head_sha,
            captured_at=captured_at,
        )


def capture(driver, repo_path: Path | str, branches) -> CaptureResult:
    """Project the full history of each branch into its own identity plane.

    Idempotent (delete-then-project per branch, then MERGE-on-id) and
    provider-free. Refuses a shallow repo before any extract (fail-closed).
    """
    repo_path = str(repo_path)
    if _is_shallow(repo_path):
        raise RuntimeError(
            f"refusing to capture a shallow repository at {repo_path!r}: "
            "full history is required (ac-5); re-clone without --depth"
        )
    branches = _validate_branches(repo_path, branches)
    repo_name = Path(repo_path).resolve().name

    create_constraints(driver)
    # scoped-rebuild: wipe each specified plane ONCE up front (never per-commit).
    for b in branches:
        wipe_branch_plane(driver, b)

    membership = _membership(repo_path, branches)
    union = _rev_list_union(repo_path, branches)

    per_branch_commits = {b: 0 for b in branches}
    for brs in membership.values():
        for b in brs:
            per_branch_commits[b] += 1

    capture_key = _US.join(branches)
    for b in branches:
        head = _head_sha(repo_path, b)
        _write_manifest(
            driver, capture_key, b, status="pending",
            commit_count=per_branch_commits[b], head_sha=head, captured_at=None,
        )

    # Dedup the EXTRACT axis: materialize + extract each unique commit ONCE, then
    # fan the SAME base IR out to each branch that contains it (scope + ingest).
    for sha in union:
        with tempfile.TemporaryDirectory() as tmp:
            _materialize_tree(repo_path, sha, tmp)
            prov = read_provenance(repo_path, sha)
            base_ir = extract(tmp, prov, repo_name=repo_name)
            augment_communities(base_ir, prov)
            for b in sorted(membership[sha]):
                ingest(driver, scope_to_branch(base_ir, b))

    # Partial-capture honesty: flip to `captured` only after ALL commits ingest.
    now = datetime.now(timezone.utc).isoformat()
    for b in branches:
        head = _head_sha(repo_path, b)
        _write_manifest(
            driver, capture_key, b, status="captured",
            commit_count=per_branch_commits[b], head_sha=head, captured_at=now,
        )

    return CaptureResult(branches=tuple(branches), commits=len(union))
