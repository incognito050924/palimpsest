"""TDD for the inferred-relation loader (wi_260702rnu).

Generalizes the Risk/DesignDecision loader pattern to plain inferred EDGES between
two EXISTING entities — CAUSALLY_RELATES / RELATES_TO / CONFLICTS_WITH (§2-bis).
No new node type: an external generator asserts a relation between two existing
graph nodes; palimpsest loads it grounded (BOTH endpoints must resolve),
entity-atomic (unresolved -> nothing written), with edge_kind='inferred' and the
rel_type restricted to a closed set. Provider-free. Live Neo4j via conftest.
"""

from palimpsest.ir import (
    CAUSALLY_RELATES,
    CLASS,
    CONFLICTS_WITH,
    METHOD,
    RELATES_TO,
    InferredRelation,
)
from palimpsest.kg import load_relations
from palimpsest.recall import recall

COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"


def _rel(source_id, target_id, rel_type=CONFLICTS_WITH, **over):
    base = dict(
        source_id=source_id, target_id=target_id, rel_type=rel_type,
        generator="fixture-rel-gen", model="m1",
        source_commit=COMMIT, created_at="2026-07-02T09:00:00+09:00",
    )
    base.update(over)
    return InferredRelation(**base)


def _two_nodes(ir):
    klass = next(n for n in ir.nodes if n.kind == CLASS)
    method = next(n for n in ir.nodes if n.kind == METHOD)
    return klass.qualified_name, method.qualified_name


def rel_count(driver, rel_type):
    with driver.session() as session:
        return session.run(
            f"MATCH ()-[e:`{rel_type}`]->() RETURN count(e) AS c"
        ).single()["c"]


def test_load_creates_inferred_relation_edge(ingested, ir):
    a, b = _two_nodes(ir)
    res = load_relations(ingested, [_rel(a, b, CONFLICTS_WITH)])
    assert res.intended == 1 and res.loaded == 1 and res.rejected == 0
    with ingested.session() as session:
        total = session.run(
            "MATCH ()-[e:CONFLICTS_WITH]->() RETURN count(e) AS c"
        ).single()["c"]
        inferred = session.run(
            "MATCH ()-[e:CONFLICTS_WITH]->() WHERE e.edge_kind='inferred' "
            "RETURN count(e) AS c"
        ).single()["c"]
    assert total == 1 and inferred == 1  # exactly one, marked inferred


def test_all_three_rel_types_supported(ingested, ir):
    a, b = _two_nodes(ir)
    res = load_relations(ingested, [
        _rel(a, b, CAUSALLY_RELATES), _rel(a, b, RELATES_TO), _rel(a, b, CONFLICTS_WITH),
    ])
    assert res.loaded == 3
    for rt in (CAUSALLY_RELATES, RELATES_TO, CONFLICTS_WITH):
        assert rel_count(ingested, rt) == 1


def test_reject_unknown_rel_type(ingested, ir):
    a, b = _two_nodes(ir)
    res = load_relations(ingested, [_rel(a, b, rel_type="MENTIONS")])
    assert res.loaded == 0 and res.rejected == 1
    assert "rel_type" in res.rejections[0].reason.lower()


def test_reject_unresolved_endpoint_is_atomic(ingested, ir):
    a, _ = _two_nodes(ir)
    res = load_relations(ingested, [_rel(a, "ghost.Nope#x()", CONFLICTS_WITH)])
    assert res.loaded == 0 and res.rejected == 1
    assert "unresolved" in res.rejections[0].reason.lower()
    assert rel_count(ingested, CONFLICTS_WITH) == 0  # atomic: nothing written


def test_reject_missing_generator(ingested, ir):
    a, b = _two_nodes(ir)
    res = load_relations(ingested, [_rel(a, b, generator="")])
    assert res.loaded == 0 and res.rejected == 1
    assert "generator" in res.rejections[0].reason.lower()


def test_reload_is_idempotent(ingested, ir):
    a, b = _two_nodes(ir)
    r = _rel(a, b, CONFLICTS_WITH)
    load_relations(ingested, [r])
    load_relations(ingested, [r])
    assert rel_count(ingested, CONFLICTS_WITH) == 1  # MERGE-on-(a,b,type)


def test_relation_not_traversed_into_items(ingested, ir):
    a, b = _two_nodes(ir)
    assert load_relations(ingested, [_rel(a, b, CONFLICTS_WITH)]).loaded == 1
    # Even if a caller explicitly asks to traverse CONFLICTS_WITH, it is filtered
    # (not in the traversal whitelist) — no item is ever reached via it.
    res = recall(ingested, a, depth=2, relations=("CONFLICTS_WITH",))
    assert all(it["relation"] != "CONFLICTS_WITH" for it in res["items"])
