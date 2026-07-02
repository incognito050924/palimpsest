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
