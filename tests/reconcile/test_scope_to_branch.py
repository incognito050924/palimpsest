"""TDD slice 1: branch-scoped identity (ir.py).

Pure, no Neo4j. ``scope_to_branch`` folds a branch into node identity so N
branches of one qualified_name coexist as DISTINCT nodes (ac-1), while
branch=None stays byte-identical to today (additive invariant).
"""

from palimpsest.ir import (
    CALLS,
    CLASS,
    METHOD,
    Edge,
    IR,
    Node,
    Provenance,
    branch_scoped_id,
    scope_to_branch,
)

PROV = Provenance(source_commit="c0", author="a", committed_at="2020-01-01T00:00:00+00:00")
QN = "kr.co.ecoletree.Foo"


def _ir() -> IR:
    return IR(
        nodes=[
            Node(kind=CLASS, qualified_name=QN, name="Foo", provenance=PROV),
            Node(kind=METHOD, qualified_name=QN + "#m()", name="m", provenance=PROV),
        ],
        edges=[Edge(kind=CALLS, src=QN + "#m()", dst=QN + "#m()", provenance=PROV)],
    )


def test_branch_none_id_is_bare_qualified_name():
    n = Node(kind=CLASS, qualified_name=QN, name="Foo", provenance=PROV)
    assert n.branch is None
    assert n.id == QN
    assert branch_scoped_id(None, QN) == QN


def test_two_branches_of_one_qualified_name_are_distinct_ids():
    a = scope_to_branch(_ir(), "feat-a")
    b = scope_to_branch(_ir(), "feat-b")
    ida = a.nodes[0].id
    idb = b.nodes[0].id
    assert ida != idb
    assert ida == f"branch:feat-a\x1f{QN}"
    assert idb == f"branch:feat-b\x1f{QN}"
    # namespace-prefixed so it can never collide with a bare qualified_name
    assert ida != QN and idb != QN


def test_scope_none_is_byte_identical():
    src = _ir()
    out = scope_to_branch(src, None)
    assert out.to_dict() == _ir().to_dict()
    for n in out.nodes:
        assert n.branch is None
        assert n.id == n.qualified_name


def test_edges_rewritten_consistently_with_scoped_node_ids():
    out = scope_to_branch(_ir(), "feat-a")
    node_ids = {n.id for n in out.nodes}
    e = out.edges[0]
    # endpoints rewritten by the SAME pure fn so resolved edges still resolve
    assert e.src == branch_scoped_id("feat-a", QN + "#m()")
    assert e.src in node_ids and e.dst in node_ids


def test_scope_to_branch_does_not_mutate_input():
    src = _ir()
    scope_to_branch(src, "feat-a")
    assert src.nodes[0].branch is None
    assert src.nodes[0].id == QN
    assert src.edges[0].src == QN + "#m()"
