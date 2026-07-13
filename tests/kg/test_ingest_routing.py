"""TDD for the SvelteKit routing ontology in KG ingest.

WHY this file exists (background for approver + consumer):

The routing slice adds four deterministic structural NODE kinds (Route, Endpoint,
Layout, Hook) and four edge kinds (REALIZES, HANDLES, LOADS, GUARDS) plus a pure
``server_only`` node marker. Ingest is fail-closed: it buckets nodes by
``nodes_by_label[n.kind]`` (KeyError on an unregistered label) and rejects an
unregistered edge kind before the Cypher ``.format`` interpolation
(ingest.py:283-287). So until the new kinds are REGISTERED in ``NODE_LABELS`` /
``REL_TYPES`` a routing IR cannot ingest at all — that is the behavior these tests
pin, end-to-end against a live Neo4j (this repo's testcontainer convention).

Each test encodes one AC clause:
  - test_routing_ontology_ingests_and_is_queryable — the 4 node kinds + 4 edge
    kinds ingest (now that they're registered) and re-query in Neo4j.
  - test_server_only_marker_round_trips — server_only=True persists as true;
    server_only=None is dropped (no phantom-false property), mirroring is_test.
  - test_unregistered_edge_kind_still_fails_closed /
    test_unregistered_node_kind_still_fails_closed — the fail-closed guards are
    NOT weakened by registering the new kinds: a truly unknown kind still raises.
"""

from collections import Counter

import pytest

from palimpsest.ir import (
    FILE,
    FUNCTION,
    ROUTE,
    ENDPOINT,
    LAYOUT,
    HOOK,
    REALIZES,
    HANDLES,
    LOADS,
    GUARDS,
    IR,
    Node,
    Edge,
    Provenance,
)


PROV = Provenance(
    source_commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    author="fixture <fixture@example.com>",
    committed_at="2026-07-13T00:00:00+09:00",
)

ROUTE_LABELS = [ROUTE, ENDPOINT, LAYOUT, HOOK]
ROUTE_RELS = [REALIZES, HANDLES, LOADS, GUARDS]

# Qualified names for a tiny, realistic SvelteKit route tree.
ROUTE_FILE_QN = "src/routes/blog/[slug]/+page.svelte"
ROUTE_QN = "/blog/[slug]"
ENDPOINT_QN = "GET /api/x"
LAYOUT_QN = "src/routes/blog/+layout.svelte::layout"
HOOK_QN = "src/hooks.server.ts"
HANDLER_FN_QN = "src/routes/api/x/+server.ts::GET"
LOAD_FN_QN = "src/routes/blog/[slug]/+page.ts::load"


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


def _routing_ir() -> IR:
    """A hand-built IR exercising every new routing node kind and edge kind."""
    return IR(
        nodes=[
            Node(kind=FILE, qualified_name=ROUTE_FILE_QN, name="+page.svelte",
                 provenance=PROV, path=ROUTE_FILE_QN),
            Node(kind=ROUTE, qualified_name=ROUTE_QN, name="/blog/[slug]",
                 provenance=PROV, path=ROUTE_FILE_QN),
            Node(kind=ENDPOINT, qualified_name=ENDPOINT_QN, name="GET /api/x",
                 provenance=PROV, path="src/routes/api/x/+server.ts",
                 server_only=True),
            Node(kind=LAYOUT, qualified_name=LAYOUT_QN, name="layout",
                 provenance=PROV, path="src/routes/blog/+layout.svelte"),
            Node(kind=HOOK, qualified_name=HOOK_QN, name="hooks.server.ts",
                 provenance=PROV, path=HOOK_QN, server_only=True),
            Node(kind=FUNCTION, qualified_name=HANDLER_FN_QN, name="GET",
                 provenance=PROV, path="src/routes/api/x/+server.ts",
                 start_line=1, end_line=3, server_only=True),
            Node(kind=FUNCTION, qualified_name=LOAD_FN_QN, name="load",
                 provenance=PROV, path="src/routes/blog/[slug]/+page.ts",
                 start_line=1, end_line=3),
        ],
        edges=[
            Edge(kind=REALIZES, src=ROUTE_FILE_QN, dst=ROUTE_QN, provenance=PROV),
            Edge(kind=HANDLES, src=HANDLER_FN_QN, dst=ENDPOINT_QN, provenance=PROV),
            Edge(kind=LOADS, src=LOAD_FN_QN, dst=ROUTE_QN, provenance=PROV),
            Edge(kind=GUARDS, src=HOOK_QN, dst=ENDPOINT_QN, provenance=PROV),
        ],
    )


def test_routing_ontology_ingests_and_is_queryable(clean_db):
    """AC: the 4 routing node kinds and 4 routing edge kinds ingest successfully
    (fail-closed ingest now ACCEPTS them because they are registered) and the
    nodes/edges are queryable in Neo4j. Before registration, ingesting a Route
    node raised KeyError at ``nodes_by_label[n.kind]`` and a REALIZES edge raised
    at the REL_TYPES guard — so this pins the new ontology end-to-end."""
    from palimpsest.kg import ingest

    ir = _routing_ir()
    ingest(clean_db, ir)

    # Each new node kind lands as exactly one labeled node.
    expected_nodes = Counter(n.kind for n in ir.nodes)
    for label in ROUTE_LABELS:
        assert label_count(clean_db, label) == expected_nodes[label] == 1, label

    # Each new edge kind lands as exactly one deterministic edge.
    for rel in ROUTE_RELS:
        assert rel_count(clean_db, rel) == 1, rel

    # The Route node round-trips its identity, and the REALIZES edge connects the
    # File to the Route as a deterministic structural edge.
    with clean_db.session() as session:
        route = session.run(
            "MATCH (n:Route {id: $id}) RETURN n.qualified_name AS qn", id=ROUTE_QN
        ).single()
        realizes = session.run(
            "MATCH (f:File {id: $fid})-[r:REALIZES]->(t:Route {id: $rid}) "
            "RETURN r.edge_kind AS kind",
            fid=ROUTE_FILE_QN, rid=ROUTE_QN,
        ).single()
        guards = session.run(
            "MATCH (h:Hook {id: $hid})-[r:GUARDS]->(e:Endpoint {id: $eid}) "
            "RETURN r.edge_kind AS kind",
            hid=HOOK_QN, eid=ENDPOINT_QN,
        ).single()
    assert route is not None and route["qn"] == ROUTE_QN
    assert realizes is not None and realizes["kind"] == "deterministic"
    assert guards is not None and guards["kind"] == "deterministic"


def test_server_only_marker_round_trips(clean_db):
    """AC: the server_only routing marker rides the generic node write. A node
    stamped ``server_only=True`` re-queries as ``n.server_only = true``; an
    unmarked node has NO server_only property at all (null is dropped by Neo4j,
    so the additive marker never phantoms a false property) — mirrors is_test."""
    from palimpsest.kg import ingest

    server_file_qn = "src/routes/x/+page.server.ts"
    client_file_qn = "src/routes/x/+page.svelte"
    ir = IR(
        nodes=[
            Node(kind=FILE, qualified_name=server_file_qn, name="+page.server.ts",
                 provenance=PROV, path=server_file_qn, server_only=True),
            Node(kind=FILE, qualified_name=client_file_qn, name="+page.svelte",
                 provenance=PROV, path=client_file_qn),  # server_only defaults None
        ],
        edges=[],
    )

    ingest(clean_db, ir)

    with clean_db.session() as session:
        marked = session.run(
            "MATCH (f:File {id: $id}) RETURN f.server_only AS s", id=server_file_qn
        ).single()["s"]
        plain = session.run(
            "MATCH (f:File {id: $id}) "
            "RETURN f.server_only AS s, 'server_only' IN keys(f) AS has",
            id=client_file_qn,
        ).single()
    assert marked is True                 # marker persisted on the server-only node
    assert plain["s"] is None             # unmarked node: property is null
    assert plain["has"] is False          # ...i.e. dropped, not a phantom false


class _NoSessionDriver:
    """A driver stub that asserts if ingest ever opens a session — proving the
    fail-closed guard fires BEFORE any Cypher / live DB is touched."""

    def session(self, *args, **kwargs):
        raise AssertionError(
            "ingest reached driver.session() — the fail-closed guard did not fire "
            "before Cypher interpolation"
        )


def test_unregistered_edge_kind_still_fails_closed(clean_db):
    """AC: registering the routing rels must NOT weaken the edge fail-closed guard.
    An edge whose kind is not in REL_TYPES (e.g. BOGUS_REL) still raises KeyError
    at the grouping boundary, before ``_REL_MERGE`` interpolation."""
    from palimpsest.kg import ingest

    a_qn, b_qn = "src/routes/a/+page.svelte", "src/routes/b/+page.svelte"
    ir = IR(
        nodes=[
            Node(kind=FILE, qualified_name=a_qn, name="+page.svelte",
                 provenance=PROV, path=a_qn),
            Node(kind=FILE, qualified_name=b_qn, name="+page.svelte",
                 provenance=PROV, path=b_qn),
        ],
        # Both endpoints resolve to real File nodes, so the edge reaches the
        # interpolation path — only its KIND is illegal.
        edges=[Edge(kind="BOGUS_REL", src=a_qn, dst=b_qn, provenance=PROV)],
    )

    with pytest.raises(KeyError):
        ingest(_NoSessionDriver(), ir)


def test_unregistered_node_kind_still_fails_closed(clean_db):
    """AC: registering the routing labels must NOT weaken the node fail-closed
    guard. A node whose kind is not in NODE_LABELS still raises KeyError at
    ``nodes_by_label[n.kind]``, before any session is opened."""
    from palimpsest.kg import ingest

    ir = IR(
        nodes=[
            Node(kind="BogusKind", qualified_name="src/routes/z/+page.svelte",
                 name="+page.svelte", provenance=PROV,
                 path="src/routes/z/+page.svelte"),
        ],
        edges=[],
    )

    with pytest.raises(KeyError):
        ingest(_NoSessionDriver(), ir)
