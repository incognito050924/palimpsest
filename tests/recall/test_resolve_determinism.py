"""TDD for label-free seed-resolution determinism (fix #5).

The kg ingest provisions a uniqueness CONSTRAINT *per (label, id)* (see
``create_constraints``), so two code nodes CAN legitimately share an ``id`` under
different labels — e.g. a Package FQN ``a.b.c`` and a Class ``c`` declared in
package ``a.b`` both have ``qualified_name == "a.b.c"``. The label-free
``MATCH (n {id: $id}) ... LIMIT 1`` in ``_RESOLVE`` then returns whichever row
Neo4j's scan happens to yield first — nondeterministic.

This pins a deterministic, rebuild-stable tie-break: among the colliding labels,
resolve the lexicographically-smallest label's node.
"""

import pytest

from palimpsest.recall.graphrag import _kind, _resolve

# A fake FQN not present in the fixture graph — the collision is planted, then
# torn down, so it never perturbs the shared session-scoped recall graph.
COLLIDE_ID = "kr.co.ecoletree.collision.Probe"


@pytest.fixture
def collision_db(recall_db):
    with recall_db.session() as s:
        # Package created first -> lower internal id -> scan order would surface it
        # first (the pre-fix, nondeterministic answer).
        s.run("MERGE (:Package {id: $id}) MERGE (:Class {id: $id})", id=COLLIDE_ID)
    try:
        yield recall_db
    finally:
        with recall_db.session() as s:
            s.run("MATCH (n {id: $id}) DELETE n", id=COLLIDE_ID)


def test_ambiguous_id_resolves_deterministically(collision_db):
    # Precondition: the collision is real — one id, two nodes, two labels.
    with collision_db.session() as s:
        labels = {
            r["l"]
            for r in s.run(
                "MATCH (n {id: $id}) RETURN head(labels(n)) AS l", id=COLLIDE_ID
            )
        }
    assert labels == {"Class", "Package"}

    resolved = _resolve(collision_db, COLLIDE_ID)
    assert resolved is not None
    # Deterministic tie-break: the lexicographically-smallest label wins
    # (Class < Package), reproducible across rebuilds — never Neo4j's scan order.
    assert _kind(resolved["labels"]) == "Class"
