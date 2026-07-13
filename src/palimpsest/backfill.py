"""Backfill the FULL git history into the KG (provider-free, deterministic).

The single-commit ingest (``cli._cmd_ingest``) projects one pinned commit. Backfill
replays that SAME extract -> ingest pipeline over EVERY commit, oldest -> newest, so
the deterministic projection reflects the whole history: every commit lands as an
``Episode`` and each code node's freshness (``committed_at``) ends up bound to the
NEWEST commit that touched it (``ingest`` MERGEs by id with an unconditional SET, so
the last write in oldest->newest order wins).

Each commit's tree is materialized into a FRESH temp dir via ``git archive`` (piped
through stdlib ``tarfile``) — never ``git checkout`` — so the working tree and HEAD
of ``repo_path`` are left untouched. Pure git + extract + ingest: no LLM, no network
beyond git and Neo4j. Re-running is idempotent (MERGE-on-id).
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from palimpsest.extract import changed_paths, dispatch, read_provenance
from palimpsest.ir import branch_scoped_id
from palimpsest.kg import (
    augment_communities,
    create_constraints,
    ingest,
    ingest_modifies,
)


@dataclass(frozen=True)
class BackfillResult:
    """What a backfill run projected.

    ``commits`` is the number of commits replayed (oldest -> newest). ``nodes`` /
    ``edges`` are the IR sizes of the NEWEST commit — the HEAD projection — not a
    cross-commit sum (a sum would double-count nodes that MERGE dedups by id).
    """

    commits: int
    nodes: int = 0
    edges: int = 0
    # Total Episode -[:MODIFIES]-> File edges landed across the whole replay (a
    # cross-commit sum, unlike ``nodes``/``edges`` which are the HEAD IR sizes):
    # MODIFIES is a per-commit fact, so every commit contributes its own edges.
    modifies: int = 0


def _commits_oldest_first(repo_path: str) -> list[str]:
    """Full SHA list, oldest -> newest. Empty repo / no commits -> ``[]``."""
    out = subprocess.run(
        ["git", "-C", repo_path, "log", "--format=%H", "--reverse"],
        check=False, capture_output=True, text=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def _materialize_tree(repo_path: str, sha: str, dest: str) -> None:
    """Extract ``sha``'s tree into ``dest`` WITHOUT touching the working tree.

    ``git archive`` streams a tar of the pinned tree to stdout; stdlib ``tarfile``
    unpacks it — no ``git checkout``, so ``repo_path``'s HEAD/working tree are
    untouched (backfill is read-only against the source repo).
    """
    archive = subprocess.run(
        ["git", "-C", repo_path, "archive", "--format=tar", sha],
        check=True, capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


def backfill(driver, repo_path: Path | str) -> BackfillResult:
    """Replay extract -> ingest over every commit, oldest -> newest.

    Idempotent (MERGE-on-id) and provider-free (pure git + extract + ingest).
    """
    repo_path = str(repo_path)
    shas = _commits_oldest_first(repo_path)
    if not shas:
        return BackfillResult(commits=0)

    # Stable Repo id across commits: each commit is materialized into a DIFFERENT
    # temp dir, so extract's default ``repo_name = root.name`` would vary per
    # commit and mint a fresh Repo node each time. Pin it to the source repo's
    # name so all commits project into the SAME Repo (and match single-commit
    # ingest, which passes the real repo path).
    repo_name = Path(repo_path).resolve().name

    create_constraints(driver)
    nodes = edges = modifies = 0
    for sha in shas:
        with tempfile.TemporaryDirectory() as tmp:
            _materialize_tree(repo_path, sha, tmp)
            prov = read_provenance(repo_path, sha)
            ir = dispatch(tmp, prov, repo_name=repo_name)
            augment_communities(ir, prov)
            ingest(driver, ir)
            # MODIFIES: bind this commit's Episode to only the File(s) it changed.
            # File ids share the bare (branch=None) plane backfill projects into, so
            # ``branch_scoped_id(None, path) == path`` — the SAME identity fn the
            # File nodes use, keeping endpoints consistent (a deleted path resolves
            # to no File node and is skipped by the writer's MATCH, never a phantom).
            rows = [
                {"episode_id": sha, "file_id": branch_scoped_id(None, path),
                 "committed_at": prov.committed_at}
                for path in changed_paths(repo_path, sha)
            ]
            modifies += ingest_modifies(driver, rows)
            nodes, edges = len(ir.nodes), len(ir.edges)

    return BackfillResult(
        commits=len(shas), nodes=nodes, edges=edges, modifies=modifies
    )
