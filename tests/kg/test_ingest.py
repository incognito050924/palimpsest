"""TDD for KG ingest: extraction IR -> Neo4j (deterministic ontology).

Vertical slices against a LIVE Neo4j (see conftest). One behavior at a time.
"""

from collections import Counter

from palimpsest.ir import REPO, PACKAGE, FILE, CLASS, METHOD, CONTAINS, IMPORTS, CALLS, DEPENDS_ON


NODE_LABELS = [REPO, PACKAGE, FILE, CLASS, METHOD]
REL_TYPES = [CONTAINS, IMPORTS, CALLS, DEPENDS_ON]

# A Method known to exist in the fixture (from the n2 extractor).
IFACE = "kr.co.ecoletree.service.commute.service.CommuteService"
SAMPLE_METHOD = IFACE + "#insertGotoWork(Map,HttpServletRequest)"


def label_count(driver, label: str) -> int:
    with driver.session() as session:
        return session.run(
            f"MATCH (n:`{label}`) RETURN count(n) AS c"
        ).single()["c"]


def rel_count(driver, rel_type: str) -> int:
    with driver.session() as session:
        return session.run(
            f"MATCH ()-[r:`{rel_type}`]->() RETURN count(r) AS c"
        ).single()["c"]


def expected_rel_counts(ir):
    """What each rel-type collapses to in Neo4j: distinct (src,dst) pairs whose
    both endpoints are real IR nodes (external/unresolved targets are dropped;
    MERGE dedups)."""
    ids = {n.id for n in ir.nodes}
    seen, counts = set(), Counter()
    for e in ir.edges:
        if e.src in ids and e.dst in ids and (e.kind, e.src, e.dst) not in seen:
            seen.add((e.kind, e.src, e.dst))
            counts[e.kind] += 1
    return counts


def test_node_counts_per_label(ingested, ir):
    # Each IR node kind lands as exactly that many labeled nodes.
    expected = Counter(n.kind for n in ir.nodes)
    for label in NODE_LABELS:
        assert label_count(ingested, label) == expected[label], label

    # Exactly one Episode node per ingested commit (single-commit v1 fixture).
    n_commits = len({n.provenance.source_commit for n in ir.nodes})
    assert label_count(ingested, "Episode") == n_commits == 1


def test_edge_counts_per_rel_type(ingested, ir):
    expected = expected_rel_counts(ir)
    assert sum(expected.values()) > 0
    for rel in REL_TYPES:
        assert rel_count(ingested, rel) == expected[rel], rel


def _edge_kind_counts(driver):
    with driver.session() as session:
        total = session.run(
            "MATCH ()-[r]->() RETURN count(r) AS c"
        ).single()["c"]
        deterministic = session.run(
            "MATCH ()-[r]->() WHERE r.edge_kind = 'deterministic' "
            "RETURN count(r) AS c"
        ).single()["c"]
        inferred = session.run(
            "MATCH ()-[r]->() WHERE r.edge_kind = 'inferred' RETURN count(r) AS c"
        ).single()["c"]
        missing = session.run(
            "MATCH ()-[r]->() WHERE r.edge_kind IS NULL RETURN count(r) AS c"
        ).single()["c"]
    return total, deterministic, inferred, missing


def test_edge_kind_partitions_deterministic_and_inferred(ingested, ir, summary_payload):
    """The no-laundering separation as a partition: every edge carries exactly one
    edge_kind, deterministic ⊎ inferred == total, no nulls. Layering the inferred
    (summary) layer on top leaves the deterministic layer's count untouched."""
    from palimpsest.kg import load_summaries

    # Deterministic-only baseline.
    total0, det0, inferred0, missing0 = _edge_kind_counts(ingested)
    expected_det = sum(expected_rel_counts(ir).values())
    assert det0 == total0 == expected_det > 0
    assert inferred0 == 0 and missing0 == 0

    # Load the inferred layer on top.
    res = load_summaries(ingested, [summary_payload])
    assert res.loaded == 1

    total, deterministic, inferred, missing = _edge_kind_counts(ingested)
    assert missing == 0
    assert deterministic + inferred == total          # partition, no overlap/gap
    assert inferred > 0
    assert deterministic == expected_det              # deterministic layer unchanged

    # Every SUMMARIZES edge is inferred; no SUMMARIZES edge is deterministic.
    with ingested.session() as session:
        summarizes_total = session.run(
            "MATCH ()-[r:SUMMARIZES]->() RETURN count(r) AS c"
        ).single()["c"]
        summarizes_inferred = session.run(
            "MATCH ()-[r:SUMMARIZES]->() WHERE r.edge_kind = 'inferred' "
            "RETURN count(r) AS c"
        ).single()["c"]
    assert summarizes_inferred == summarizes_total == inferred > 0


def test_every_edge_carries_provenance_and_freshness(ingested, ir):
    # DESIGN §2-bis / ingest docstring: provenance (source_commit, author) AND
    # freshness (code_bound_at) are edge properties — none may be null.
    with ingested.session() as session:
        total = session.run(
            "MATCH ()-[r]->() RETURN count(r) AS c"
        ).single()["c"]
        missing = session.run(
            "MATCH ()-[r]->() "
            "WHERE r.source_commit IS NULL OR r.author IS NULL "
            "OR r.code_bound_at IS NULL "
            "RETURN count(r) AS c"
        ).single()["c"]
    assert total > 0
    assert missing == 0


def test_method_node_resolves_to_real_code_with_provenance(ingested, ir):
    ir_method = ir.node(SAMPLE_METHOD)
    assert ir_method is not None  # guard the fixture assumption

    with ingested.session() as session:
        rec = session.run(
            "MATCH (m:Method {id: $id}) RETURN m",
            id=SAMPLE_METHOD,
        ).single()
    assert rec is not None
    m = rec["m"]

    # file:line grounding matches the IR exactly
    assert m["qualified_name"] == SAMPLE_METHOD
    assert m["path"] == ir_method.path
    assert m["start_line"] == ir_method.start_line
    assert m["end_line"] == ir_method.end_line

    # provenance + freshness stamped on the node
    prov = ir_method.provenance
    assert m["source_commit"] == prov.source_commit
    assert m["author"] == prov.author
    assert m["code_bound_at"] == prov.committed_at  # v1 single-commit


def test_reingest_is_idempotent(clean_db, ir):
    from palimpsest.kg import ingest

    ingest(clean_db, ir)
    first_nodes = {label: label_count(clean_db, label)
                   for label in NODE_LABELS + ["Episode"]}
    first_rels = {rel: rel_count(clean_db, rel) for rel in REL_TYPES}

    ingest(clean_db, ir)  # MERGE-on-id -> no duplicates
    second_nodes = {label: label_count(clean_db, label)
                    for label in NODE_LABELS + ["Episode"]}
    second_rels = {rel: rel_count(clean_db, rel) for rel in REL_TYPES}

    assert second_nodes == first_nodes
    assert second_rels == first_rels
