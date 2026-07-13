"""TDD for Class-level Community detection (wi_2607010n6).

Vertical slices: pure-Python partition first, then materialize-in-IR + ingest
against a LIVE Neo4j (see conftest), then re-ingest idempotency.
"""

import copy

import pytest

from palimpsest.ir import (
    CALLS,
    CLASS,
    COMMUNITY,
    CONTAINS,
    FILE,
    FUNCTION,
    MEMBER_OF,
    REPO,
    Edge,
    IR,
    Node,
    Provenance,
)
from palimpsest.kg import (
    augment_communities,
    community_id,
    compute_communities,
    ingest,
)

CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
SVC = "kr.co.ecoletree.service.commute.service.CommuteService"


@pytest.fixture
def augmented_ir(ir):
    """A private, deep-copied IR with the Community partition materialized in,
    stamped with the corpus-level (Repo) provenance — so the shared session
    ``ir`` fixture is never mutated."""
    aug = copy.deepcopy(ir)
    prov = next(n for n in aug.nodes if n.kind == REPO).provenance
    augment_communities(aug, prov)
    return aug


@pytest.fixture
def community_ingested(clean_db, augmented_ir):
    """A clean DB with the Community-augmented IR ingested once."""
    ingest(clean_db, augmented_ir)
    return clean_db, augmented_ir


def test_cross_package_linked_classes_cluster_into_one_community(ir):
    # ac-2: CommuteController and CommuteService are in different packages but are
    # linked by cross-package CALLS + DEPENDS_ON, so they land in ONE Community.
    parts = compute_communities(ir)

    # Exclusive, flat partition: every Class in exactly one community.
    all_classes = sorted(n.qualified_name for n in ir.nodes if n.kind == CLASS)
    flat = sorted(c for part in parts for c in part)
    assert flat == all_classes
    assert sum(len(p) for p in parts) == len(all_classes)  # no double-count

    comm = next(p for p in parts if CTRL in p)
    assert SVC in comm  # the two linked classes share one community


def _kotlin_modules_ir() -> IR:
    """A pure hand-built IR modelling two Kotlin modules (Files carrying only
    top-level Functions, no Class) plus a third isolated module.

    Module A's top-level function CALLS module B's top-level function, so at the
    grouping-container level the two Files must land in one Community. Module C
    is unlinked and forms its own singleton. This exercises the generalization
    from Class-only grouping to unit-of grouping (Method->Class, Function->File)
    without any Neo4j round-trip — it is a pure union-find assertion over the IR.
    """
    prov = Provenance(source_commit="deadbeef", author="kt <kt@x>", committed_at="t0")

    def n(kind: str, qn: str) -> Node:
        return Node(kind=kind, qualified_name=qn, name=qn, provenance=prov)

    nodes = [
        n(FILE, "src/A.kt"),
        n(FILE, "src/B.kt"),
        n(FILE, "src/C.kt"),
        n(FUNCTION, "app.a.topA"),
        n(FUNCTION, "app.b.topB"),
        n(FUNCTION, "app.c.topC"),
    ]
    edges = [
        Edge(kind=CONTAINS, src="src/A.kt", dst="app.a.topA", provenance=prov),
        Edge(kind=CONTAINS, src="src/B.kt", dst="app.b.topB", provenance=prov),
        Edge(kind=CONTAINS, src="src/C.kt", dst="app.c.topC", provenance=prov),
        # cross-module top-level call: A.topA -> B.topB
        Edge(kind=CALLS, src="app.a.topA", dst="app.b.topB", provenance=prov),
    ]
    return IR(nodes=nodes, edges=edges)


def test_cross_module_top_level_functions_cluster_into_one_community():
    # ac-3: two Kotlin modules whose top-level Functions CALL each other are
    # grouped by their containing File (Module) and land in ONE Community.
    parts = compute_communities(_kotlin_modules_ir())

    # exclusive, flat partition over the grouping containers (the three Files)
    flat = sorted(c for part in parts for c in part)
    assert flat == ["src/A.kt", "src/B.kt", "src/C.kt"]

    comm = next(p for p in parts if "src/A.kt" in p)
    assert "src/B.kt" in comm          # linked modules share one community
    assert ["src/C.kt"] in parts       # isolated module is its own singleton


def _count(driver, cypher, **params):
    with driver.session() as session:
        return session.run(cypher, **params).single()["c"]


def test_ingest_creates_community_node_and_member_of_edges(community_ingested):
    # ac-1: augment-then-ingest materializes a Community node per component and a
    # MEMBER_OF edge per member Class, edge_kind=deterministic.
    driver, aug = community_ingested
    parts = compute_communities(aug)
    n_members = sum(len(p) for p in parts)

    assert _count(driver, "MATCH (c:Community) RETURN count(c) AS c") == len(parts)
    assert _count(driver, "MATCH ()-[r:MEMBER_OF]->() RETURN count(r) AS c") == n_members

    # Every MEMBER_OF is (:Class)->(:Community), edge_kind=deterministic.
    bad = _count(
        driver,
        "MATCH (a)-[r:MEMBER_OF]->(b) "
        "WHERE NOT ('Class' IN labels(a) AND 'Community' IN labels(b)) "
        "OR r.edge_kind <> 'deterministic' RETURN count(r) AS c",
    )
    assert bad == 0

    # ac-1 partition invariant: deterministic ⊎ inferred == total, no nulls —
    # holds over ALL edges, now including MEMBER_OF.
    total = _count(driver, "MATCH ()-[r]->() RETURN count(r) AS c")
    det = _count(driver, "MATCH ()-[r]->() WHERE r.edge_kind='deterministic' RETURN count(r) AS c")
    inferred = _count(driver, "MATCH ()-[r]->() WHERE r.edge_kind='inferred' RETURN count(r) AS c")
    missing = _count(driver, "MATCH ()-[r]->() WHERE r.edge_kind IS NULL RETURN count(r) AS c")
    assert missing == 0
    assert det + inferred == total
    assert inferred == 0  # no summaries loaded here

    # Provenance + freshness on MEMBER_OF (test_ingest asserts this graph-wide).
    prov_missing = _count(
        driver,
        "MATCH ()-[r:MEMBER_OF]->() "
        "WHERE r.source_commit IS NULL OR r.author IS NULL OR r.code_bound_at IS NULL "
        "RETURN count(r) AS c",
    )
    assert prov_missing == 0


def test_community_clusters_controller_and_service_together(community_ingested):
    # ac-2 at the graph level: the two cross-package classes MEMBER_OF the same
    # Community node.
    driver, _ = community_ingested
    with driver.session() as session:
        rows = session.run(
            "MATCH (c:Class)-[:MEMBER_OF]->(comm:Community) "
            "WHERE c.id IN [$a, $b] RETURN comm.id AS cid",
            a=CTRL, b=SVC,
        )
        cids = {r["cid"] for r in rows}
    assert len(cids) == 1  # both in one community


def _membership(driver):
    with driver.session() as session:
        rows = session.run(
            "MATCH (c:Class)-[:MEMBER_OF]->(comm:Community) "
            "RETURN c.id AS cls, comm.id AS comm ORDER BY cls"
        )
        return {r["cls"]: r["comm"] for r in rows}


def test_reingest_is_idempotent_for_communities(clean_db, augmented_ir):
    # ac-3: rebuild/re-ingest -> identical Community membership and identical
    # Community ids (MERGE-on-id + sorted-member id = rebuild-stable).
    ingest(clean_db, augmented_ir)
    first_comm = _count(clean_db, "MATCH (c:Community) RETURN count(c) AS c")
    first_member = _count(clean_db, "MATCH ()-[r:MEMBER_OF]->() RETURN count(r) AS c")
    first_map = _membership(clean_db)

    ingest(clean_db, augmented_ir)  # re-ingest same IR
    assert _count(clean_db, "MATCH (c:Community) RETURN count(c) AS c") == first_comm
    assert _count(clean_db, "MATCH ()-[r:MEMBER_OF]->() RETURN count(r) AS c") == first_member
    assert _membership(clean_db) == first_map


def test_community_id_is_rebuild_stable(ir):
    # ac-3: two INDEPENDENT augmentations of the same code yield identical
    # Community ids (id is a hash of the sorted member set, order-free).
    a = copy.deepcopy(ir)
    b = copy.deepcopy(ir)
    prov_a = next(n for n in a.nodes if n.kind == REPO).provenance
    prov_b = next(n for n in b.nodes if n.kind == REPO).provenance
    augment_communities(a, prov_a)
    augment_communities(b, prov_b)
    ids_a = sorted(n.qualified_name for n in a.nodes if n.kind == COMMUNITY)
    ids_b = sorted(n.qualified_name for n in b.nodes if n.kind == COMMUNITY)
    assert ids_a == ids_b and ids_a  # non-empty and identical
