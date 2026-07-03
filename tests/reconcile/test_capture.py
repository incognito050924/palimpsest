"""TDD slice 4: N-way branch capture (reconcile.py).

Behavioral, against a LIVE Neo4j + a hermetic multi-branch git repo (conftest).
Covers this node's AC:
  ac-1  N branches of one symbol coexist as distinct nodes (no collapse)
  ac-5  git = SoT, full history preserved, idempotent rebuild
  ac-6  only specified branches captured; unspecified excluded
plus the contract's fail-closed / git-safe / partial-capture-honesty behaviors.
"""

import subprocess

import pytest

from palimpsest.extract import read_provenance
from palimpsest.reconcile import capture

CLASS_QN = "kr.co.ecoletree.service.commute.service.CommuteService"


def _git(repo, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _rev_list(repo, *refs) -> list[str]:
    out = _git(repo, "rev-list", *refs)
    return [x for x in out.splitlines() if x]


def _scalar(driver, cypher, **params):
    with driver.session() as session:
        return session.run(cypher, **params).single()[0]


# ---- ac-1: distinct coexisting branch planes ---------------------------------

def test_two_branches_of_one_symbol_are_distinct_nodes(db, multi_branch_repo):
    repo = multi_branch_repo
    capture(db, repo, ["main", "feature"])

    # Same qualified_name, two distinct branch-scoped Class ids.
    main_id = f"branch:main\x1f{CLASS_QN}"
    feat_id = f"branch:feature\x1f{CLASS_QN}"
    assert _scalar(db, "MATCH (c:Class {id:$i}) RETURN count(c)", i=main_id) == 1
    assert _scalar(db, "MATCH (c:Class {id:$i}) RETURN count(c)", i=feat_id) == 1

    # Freshness diverges: each plane's newest touching commit differs (no collapse).
    main_tip = read_provenance(repo, "main").committed_at
    feat_tip = read_provenance(repo, "feature").committed_at
    assert main_tip != feat_tip
    assert _scalar(db, "MATCH (c:Class {id:$i}) RETURN c.committed_at", i=main_id) == main_tip
    assert _scalar(db, "MATCH (c:Class {id:$i}) RETURN c.committed_at", i=feat_id) == feat_tip

    # The branch-unique method lives only on its own plane.
    on_main = _scalar(
        db,
        "MATCH (m:Method {branch:'main'}) WHERE m.qualified_name CONTAINS 'selectOnMain' RETURN count(m)",
    )
    on_feat_wrong = _scalar(
        db,
        "MATCH (m:Method {branch:'feature'}) WHERE m.qualified_name CONTAINS 'selectOnMain' RETURN count(m)",
    )
    assert on_main == 1 and on_feat_wrong == 0


# ---- ac-6: only specified branches captured ----------------------------------

def test_only_specified_branches_are_captured(db, multi_branch_repo):
    capture(db, multi_branch_repo, ["main"])

    assert _scalar(db, "MATCH (n {branch:'main'}) RETURN count(n)") > 0
    # feature was NOT specified -> its plane must not exist.
    assert _scalar(db, "MATCH (n {branch:'feature'}) RETURN count(n)") == 0


# ---- ac-5: full history + idempotent rebuild ---------------------------------

def test_full_history_lands_as_episodes_and_rebuild_is_idempotent(db, multi_branch_repo):
    repo = multi_branch_repo
    union = set(_rev_list(repo, "main", "feature"))

    capture(db, repo, ["main", "feature"])
    assert _scalar(db, "MATCH (e:Episode) RETURN count(e)") == len(union)
    with db.session() as session:
        ep_ids = {r["id"] for r in session.run("MATCH (e:Episode) RETURN e.id AS id")}
    assert ep_ids == union  # every reachable commit is an Episode (branch-agnostic spine)

    classes_first = _scalar(db, "MATCH (c:Class) RETURN count(c)")
    capture(db, repo, ["main", "feature"])  # rebuild
    assert _scalar(db, "MATCH (e:Episode) RETURN count(e)") == len(union)
    assert _scalar(db, "MATCH (c:Class) RETURN count(c)") == classes_first


def test_shared_base_projected_into_both_planes(db, multi_branch_repo):
    # The base commit is shared; after fan-out the base Class exists on BOTH planes.
    capture(db, multi_branch_repo, ["main", "feature"])
    assert _scalar(db, "MATCH (c:Class {id:$i}) RETURN count(c)", i=f"branch:main\x1f{CLASS_QN}") == 1
    assert _scalar(db, "MATCH (c:Class {id:$i}) RETURN count(c)", i=f"branch:feature\x1f{CLASS_QN}") == 1


# ---- partial-capture honesty: manifest ---------------------------------------

def test_manifest_marks_each_branch_captured(db, multi_branch_repo):
    repo = multi_branch_repo
    capture(db, repo, ["main", "feature"])

    for b in ("main", "feature"):
        with db.session() as session:
            row = session.run(
                "MATCH (m:CaptureManifest {branch:$b}) RETURN m.status AS status, "
                "m.commit_count AS cc, m.head_sha AS head",
                b=b,
            ).single()
        assert row["status"] == "captured"
        assert row["cc"] == len(_rev_list(repo, b))
        assert row["head"] == _git(repo, "rev-parse", b).strip()


# ---- fail-closed: shallow repository -----------------------------------------

def test_refuses_shallow_repository(db, multi_branch_repo, tmp_path):
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--depth", "1", "file://" + str(multi_branch_repo), str(shallow)],
        check=True, capture_output=True, text=True,
    )
    with pytest.raises(Exception) as exc:
        capture(db, shallow, ["main"])
    assert "shallow" in str(exc.value).lower()


# ---- git-safe branch args ----------------------------------------------------

def test_nonexistent_branch_is_explicitly_rejected(db, multi_branch_repo):
    with pytest.raises(ValueError) as exc:
        capture(db, multi_branch_repo, ["no-such-branch"])
    assert "no-such-branch" in str(exc.value)


def test_duplicate_branch_args_are_deduped(db, multi_branch_repo):
    # [main, main] -> one plane, one manifest, no error.
    capture(db, multi_branch_repo, ["main", "main"])
    assert _scalar(db, "MATCH (m:CaptureManifest {branch:'main'}) RETURN count(m)") == 1
