"""TDD for backfill: replay extract -> ingest over the FULL git history.

Behavioral slices against a LIVE Neo4j + a hermetic 2-commit git repo (conftest).
Commit SHAs are non-deterministic (timestamps), so assertions are on COUNTS and
on equality-to-``git log`` output — never hardcoded SHAs.
"""

import subprocess

from palimpsest.backfill import backfill
from palimpsest.extract import read_provenance

CLASS_QN = "kr.co.ecoletree.service.commute.service.CommuteService"


def _git_log_shas(repo) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%H", "--reverse"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def _scalar(driver, cypher: str, **params) -> int:
    with driver.session() as session:
        return session.run(cypher, **params).single()[0]


def test_backfill_creates_episode_per_commit(db, repo):
    backfill(db, repo)

    assert _scalar(db, "MATCH (e:Episode) RETURN count(e)") == 2

    with db.session() as session:
        episode_ids = {
            r["id"]
            for r in session.run("MATCH (e:Episode) RETURN e.id AS id")
        }
    assert episode_ids == set(_git_log_shas(repo))


def test_backfill_head_nodes_reflect_newest_commit(db, repo):
    backfill(db, repo)

    shas = _git_log_shas(repo)
    newest_committed_at = read_provenance(repo, shas[-1]).committed_at
    oldest_committed_at = read_provenance(repo, shas[0]).committed_at
    assert newest_committed_at != oldest_committed_at  # guard: dates are distinct

    bound = _scalar(
        db,
        "MATCH (c:Class {id: $id}) RETURN c.committed_at",
        id=CLASS_QN,
    )
    assert bound == newest_committed_at


def test_backfill_is_idempotent(db, repo):
    first = backfill(db, repo)
    classes_after_first = _scalar(db, "MATCH (c:Class) RETURN count(c)")
    # Single stable Repo across every commit's (distinct) temp dir — guards the
    # repo_name pinning; without it each commit would mint a fresh Repo node.
    assert _scalar(db, "MATCH (r:Repo) RETURN count(r)") == 1

    second = backfill(db, repo)

    assert first.commits == second.commits == 2
    assert _scalar(db, "MATCH (e:Episode) RETURN count(e)") == 2
    assert _scalar(db, "MATCH (c:Class) RETURN count(c)") == classes_after_first
    assert _scalar(db, "MATCH (r:Repo) RETURN count(r)") == 1  # still one after re-run


SVC_FILE = "CommuteService.java"  # the fixture repo lays the file at its root


def test_backfill_binds_modifies_to_each_commits_changed_files(db, repo):
    """ac-1: every commit's Episode MODIFIES only the File(s) it changed, and the
    ROOT commit (no parent) is captured too (``--root``) — else the initial import
    would silently drop the file it introduced."""
    result = backfill(db, repo)

    # Both commits touched CommuteService.java (root ADDs it, c2 EDITs it), so two
    # MODIFIES edges land — the root commit among them proves --root captured it.
    assert result.modifies == 2
    assert _scalar(
        db, "MATCH (:Episode)-[r:MODIFIES]->(:File {id:$f}) RETURN count(r)", f=SVC_FILE
    ) == 2

    # ac-1 grounding: each Episode binds to exactly the one File it changed.
    with db.session() as session:
        degrees = sorted(
            r["d"]
            for r in session.run(
                "MATCH (e:Episode) RETURN COUNT { (e)-[:MODIFIES]->() } AS d"
            )
        )
    assert degrees == [1, 1]

    # ac-1: the edge is deterministic, and the ROOT Episode is one of the two.
    assert _scalar(
        db,
        "MATCH (:Episode)-[r:MODIFIES]->(:File {id:$f}) "
        "WHERE r.edge_kind = 'deterministic' RETURN count(r)", f=SVC_FILE,
    ) == 2


def test_backfill_modifies_is_idempotent_and_head_merge_holds(db, repo):
    """ac-2: re-backfill converges — no duplicate MODIFIES edges, and File keeps
    its HEAD-MERGE invariant (one CommuteService.java File, no per-commit versioned
    code nodes)."""
    first = backfill(db, repo)
    files_after_first = _scalar(db, "MATCH (f:File) RETURN count(f)")

    second = backfill(db, repo)

    assert first.modifies == second.modifies == 2
    assert _scalar(db, "MATCH ()-[r:MODIFIES]->() RETURN count(r)") == 2  # no dup
    assert _scalar(db, "MATCH (f:File) RETURN count(f)") == files_after_first
    assert _scalar(db, "MATCH (f:File {id:$f}) RETURN count(f)", f=SVC_FILE) == 1


def test_backfill_does_not_mutate_repo(db, repo):
    def status() -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain"],
            check=True, capture_output=True, text=True,
        ).stdout

    def head() -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout

    status_before, head_before = status(), head()
    backfill(db, repo)
    assert status() == status_before
    assert head() == head_before
