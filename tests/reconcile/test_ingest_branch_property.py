"""TDD slice 2: branch persisted as a node property (the GC discriminator).

A scoped IR's nodes carry ``n.branch = <branch>``; a bare-id (single-branch)
node gets ``branch = null``. Live Neo4j.
"""

from palimpsest.ir import CLASS, IR, Node, Provenance, scope_to_branch
from palimpsest.kg import create_constraints, ingest

PROV = Provenance(source_commit="c0", author="a", committed_at="2020-01-01T00:00:00+00:00")
QN = "kr.co.ecoletree.Foo"


def _one_class_ir() -> IR:
    return IR(nodes=[Node(kind=CLASS, qualified_name=QN, name="Foo", provenance=PROV)])


def test_scoped_node_carries_branch_property(db):
    create_constraints(db)
    ingest(db, scope_to_branch(_one_class_ir(), "feat-a"))

    with db.session() as session:
        row = session.run(
            "MATCH (c:Class) RETURN c.branch AS branch, c.id AS id"
        ).single()
    assert row["branch"] == "feat-a"
    assert row["id"] == f"branch:feat-a\x1f{QN}"


def test_bare_node_has_null_branch(db):
    create_constraints(db)
    ingest(db, _one_class_ir())

    with db.session() as session:
        row = session.run(
            "MATCH (c:Class {id: $id}) RETURN c.branch AS branch", id=QN
        ).single()
    assert row["branch"] is None
