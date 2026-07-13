"""ac-7 regression guard: the SvelteKit routing ontology must NOT perturb the
existing Class-level Community partition.

WHY this file exists (background for approver + consumer):

The routing slice added four NODE kinds (Route/Endpoint/Layout/Hook) and four
EDGE kinds (REALIZES/HANDLES/LOADS/GUARDS). Community detection
(``palimpsest.kg.community``) is defined ONLY over the pre-existing structural
ontology:

  * ``_containers`` / ``_unit_of`` range over Class ∪ Module (a File that directly
    CONTAINS a top-level Function). A Route/Endpoint/Layout/Hook is none of
    {Class, File, Function, Method}, so a routing NODE can never become a
    grouping container / community member.
  * ``_unit_level_pairs`` consumes ONLY ``CALLS`` + ``DEPENDS_ON``. REALIZES /
    HANDLES / LOADS / GUARDS are none of those, so a routing EDGE can never forge
    a cross-container link.

So the invariant holds BY CONSTRUCTION — these tests PIN it against future drift.
Each test encodes one ac-7 clause:

  - test_non_svelte_partition_identical_after_routing_layer — add a full SvelteKit
    routing subgraph to a non-Svelte (Java) repo, recompute the partition, and the
    existing non-Svelte communities are byte/structure-identical (never split,
    merged, or joined to a routing node).
  - test_routing_edges_never_enter_unit_level_pairs — even routing edges FORGED
    between two real containers are ignored, because consumption is by edge KIND
    (CALLS/DEPENDS_ON), not node membership; and in a realistic mixed repo no
    ``_unit_level_pairs`` link ever contains a routing node.
  - test_member_of_for_non_svelte_classes_unchanged_with_routing — at the GRAPH
    level (live Neo4j), every non-Svelte Class keeps the SAME Community id whether
    or not the routing layer is ingested alongside it.
"""

import copy

from palimpsest.extract.sveltekit import extract_sveltekit_routes
from palimpsest.ir import (
    CONTAINS,
    ENDPOINT,
    FILE,
    FUNCTION,
    GUARDS,
    HANDLES,
    HOOK,
    LAYOUT,
    LOADS,
    REALIZES,
    REPO,
    ROUTE,
    Edge,
    IR,
    Node,
    Provenance,
)
from palimpsest.kg import (
    augment_communities,
    compute_communities,
    create_constraints,
    ingest,
)
from palimpsest.kg.community import _containers, _unit_level_pairs

PROV = Provenance(source_commit="deadbeef", author="rt <rt@x>", committed_at="t0")


def _sveltekit_source_ir() -> IR:
    """A tiny SvelteKit SOURCE subgraph — the exact shape
    :func:`extract_sveltekit_routes` reads: FILE nodes plus the handler/load
    FUNCTION nodes they CONTAIN. The ``+page.ts`` and ``+server.ts`` files DO carry
    top-level Functions, so they become Modules (containers) — the realistic
    mixed-repo case where the routing layer brings its OWN containers, which must
    still never perturb the non-Svelte partition.
    """
    def n(kind, qn, name, path, sl=None, el=None):
        return Node(
            kind=kind, qualified_name=qn, name=name, provenance=PROV,
            path=path, start_line=sl, end_line=el,
        )

    page_svelte = "src/routes/blog/[slug]/+page.svelte"
    page_ts = "src/routes/blog/[slug]/+page.ts"
    server_ts = "src/routes/api/x/+server.ts"
    layout_svelte = "src/routes/blog/+layout.svelte"
    hook = "src/hooks.server.ts"
    load_fn = f"{page_ts}::load"
    get_fn = f"{server_ts}::GET"

    nodes = [
        n(FILE, page_svelte, "+page.svelte", page_svelte, 1, 10),
        n(FILE, page_ts, "+page.ts", page_ts, 1, 10),
        n(FILE, server_ts, "+server.ts", server_ts, 1, 10),
        n(FILE, layout_svelte, "+layout.svelte", layout_svelte, 1, 10),
        n(FILE, hook, "hooks.server.ts", hook, 1, 10),
        n(FUNCTION, load_fn, "load", page_ts, 1, 3),
        n(FUNCTION, get_fn, "GET", server_ts, 1, 3),
    ]
    edges = [
        Edge(CONTAINS, page_ts, load_fn, PROV),
        Edge(CONTAINS, server_ts, get_fn, PROV),
    ]
    return IR(nodes=nodes, edges=edges)


def _routing_layer():
    """The derived routing (nodes, edges) — exercises all four routing node kinds
    (Route/Endpoint/Layout/Hook) and all four edge kinds (REALIZES/HANDLES/LOADS/
    GUARDS)."""
    svelte = _sveltekit_source_ir()
    route_nodes, route_edges = extract_sveltekit_routes(svelte.nodes, PROV)
    return svelte, route_nodes, route_edges


def test_non_svelte_partition_identical_after_routing_layer(ir):
    # ac-7: partition the non-Svelte (Java) repo, THEN add a full SvelteKit routing
    # subgraph and recompute — the existing non-Svelte communities are UNCHANGED.
    base_parts = compute_communities(ir)
    base_containers = _containers(ir)

    svelte, route_nodes, route_edges = _routing_layer()
    # Sanity the fixture actually exercises every routing node kind (else the guard
    # would be vacuous).
    assert {n.kind for n in route_nodes} == {ROUTE, ENDPOINT, LAYOUT, HOOK}

    mixed = IR(
        nodes=ir.nodes + svelte.nodes + route_nodes,
        edges=ir.edges + svelte.edges + route_edges,
    )
    mixed_parts = compute_communities(mixed)

    # Restrict every mixed community to the base (non-Svelte) containers, drop the
    # now-empty Svelte-only communities — what remains is byte/structure-identical
    # to the baseline partition.
    restricted = sorted(
        sorted(c for c in part if c in base_containers)
        for part in mixed_parts
        if any(c in base_containers for c in part)
    )
    assert restricted == base_parts

    # Stronger: each base community survives WHOLE — neither split nor merged by the
    # routing layer.
    for part in base_parts:
        assert part in mixed_parts

    # No routing NODE is ever a community member (routing nodes are not containers).
    routing_qns = {n.qualified_name for n in route_nodes}
    all_members = {c for part in mixed_parts for c in part}
    assert routing_qns.isdisjoint(all_members)


def test_routing_edges_never_enter_unit_level_pairs(ir):
    # ac-7: _unit_level_pairs consumes edges by KIND (CALLS + DEPENDS_ON only), not
    # by node membership. Forge routing edges between two REAL containers: if the
    # filter keyed off endpoints instead of edge kind, these would forge a spurious
    # cross-container link. They must be ignored.
    base_pairs = _unit_level_pairs(ir)

    containers = sorted(_containers(ir))
    assert len(containers) >= 2  # the Java fixture has >=2 Class containers
    a, b = containers[0], containers[1]
    forged = [
        Edge(REALIZES, a, b, PROV),
        Edge(HANDLES, a, b, PROV),
        Edge(LOADS, a, b, PROV),
        Edge(GUARDS, b, a, PROV),
    ]
    pathological = IR(nodes=list(ir.nodes), edges=ir.edges + forged)
    assert _unit_level_pairs(pathological) == base_pairs

    # And in a realistic mixed repo, no cross-container pair ever contains a routing
    # node, and the base pairs are preserved unchanged (the Svelte Modules add no
    # CALLS/DEPENDS_ON, so no new links appear).
    svelte, route_nodes, route_edges = _routing_layer()
    mixed = IR(
        nodes=ir.nodes + svelte.nodes + route_nodes,
        edges=ir.edges + svelte.edges + route_edges,
    )
    routing_qns = {n.qualified_name for n in route_nodes}
    mixed_pairs = _unit_level_pairs(mixed)
    for pair in mixed_pairs:
        assert routing_qns.isdisjoint(pair)
    assert mixed_pairs == base_pairs


def _class_member_of(driver) -> dict:
    """{Class id -> Community id} for every (:Class)-[:MEMBER_OF]->(:Community)."""
    with driver.session() as session:
        rows = session.run(
            "MATCH (c:Class)-[:MEMBER_OF]->(comm:Community) "
            "RETURN c.id AS cls, comm.id AS comm ORDER BY cls"
        )
        return {r["cls"]: r["comm"] for r in rows}


def test_member_of_for_non_svelte_classes_unchanged_with_routing(clean_db, ir):
    # ac-7 at the GRAPH level: ingest the Java code alone, snapshot each Class's
    # Community; then ingest the SAME code PLUS the routing layer (communities
    # augmented over the full mixed IR, the realistic pipeline) and confirm every
    # non-Svelte Class keeps the SAME Community id.
    prov = next(n for n in ir.nodes if n.kind == REPO).provenance

    base_aug = copy.deepcopy(ir)
    augment_communities(base_aug, prov)
    ingest(clean_db, base_aug)
    base_map = _class_member_of(clean_db)
    assert base_map  # the fixture yields Class -> Community membership

    # Fresh DB, then ingest Java + SvelteKit routing layer together.
    with clean_db.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    create_constraints(clean_db)

    mixed = IR(nodes=copy.deepcopy(ir.nodes), edges=copy.deepcopy(ir.edges))
    svelte, route_nodes, route_edges = _routing_layer()
    mixed.nodes += svelte.nodes + route_nodes
    mixed.edges += svelte.edges + route_edges
    augment_communities(mixed, prov)
    ingest(clean_db, mixed)
    mixed_map = _class_member_of(clean_db)

    # Every non-Svelte Class keeps the SAME Community id — the routing layer never
    # re-partitioned the existing code.
    assert mixed_map == base_map
