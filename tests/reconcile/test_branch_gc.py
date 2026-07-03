"""TDD slice 3: branch-plane GC — scoped-rebuild (2a) and reaper (2b).

Both keyed on the ``branch`` node property. The ``branch IS NOT NULL`` guard on
the reaper means the bare-id (unspecified) plane is NEVER reaped (ac-6). Live
Neo4j.
"""

import pytest

from palimpsest.ir import CLASS, IR, Node, Provenance, scope_to_branch
from palimpsest.kg import create_constraints, ingest
from palimpsest.kg.ingest import reap_dead_branches, wipe_branch_plane

PROV = Provenance(source_commit="c0", author="a", committed_at="2020-01-01T00:00:00+00:00")


def _ir(*qns: str) -> IR:
    return IR(nodes=[Node(kind=CLASS, qualified_name=q, name=q, provenance=PROV) for q in qns])


def _branch_ids(driver, branch):
    with driver.session() as session:
        return {
            r["id"]
            for r in session.run(
                "MATCH (n {branch:$b}) RETURN n.id AS id", b=branch
            )
        }


def _bare_class_count(driver):
    # Only Class nodes: Episodes are intentionally bare (branch-agnostic history
    # spine), so counting ALL bare nodes would include them.
    with driver.session() as session:
        return session.run(
            "MATCH (n:Class) WHERE n.branch IS NULL RETURN count(n) AS c"
        ).single()["c"]


def test_wipe_branch_plane_deletes_stale_then_reproject(db):
    create_constraints(db)
    # first projection of branch B: two classes
    ingest(db, scope_to_branch(_ir("A", "B"), "featB"))
    assert len(_branch_ids(db, "featB")) == 2

    # tip moved: B no longer contains class B — delete-then-project
    wipe_branch_plane(db, "featB")
    ingest(db, scope_to_branch(_ir("A"), "featB"))

    ids = _branch_ids(db, "featB")
    assert ids == {"branch:featB\x1fA"}  # stale "B" gone, only A remains


def test_wipe_branch_plane_never_touches_bare(db):
    create_constraints(db)
    ingest(db, _ir("Bare"))            # bare-id plane
    ingest(db, scope_to_branch(_ir("X"), "featB"))
    wipe_branch_plane(db, "featB")
    assert _bare_class_count(db) == 1        # bare untouched
    assert _branch_ids(db, "featB") == set()


def test_wipe_branch_plane_refuses_none(db):
    with pytest.raises(ValueError):
        wipe_branch_plane(db, None)


def test_reaper_drops_non_live_branch_keeps_live_and_bare(db):
    create_constraints(db)
    ingest(db, _ir("Bare"))
    ingest(db, scope_to_branch(_ir("L"), "live"))
    ingest(db, scope_to_branch(_ir("D"), "dead"))

    reap_dead_branches(db, ["live"])

    assert _branch_ids(db, "dead") == set()          # non-live reaped
    assert _branch_ids(db, "live") == {"branch:live\x1fL"}  # live kept
    assert _bare_class_count(db) == 1                       # bare NEVER reaped
