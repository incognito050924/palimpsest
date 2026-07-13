"""ac-7 regression guard: the SvelteKit routing ontology must NOT change what
ordinary (non-routing) recall returns.

WHY this file exists (background for approver + consumer):

Main recall (``palimpsest.recall.graphrag.recall``) walks ONLY the deterministic
structural whitelist ``DEFAULT_RELATIONS = (CALLS, DEPENDS_ON, CONTAINS, IMPORTS)``
(graphrag.py:63), and ``_neighbors`` re-filters any caller-supplied relation set
down to that whitelist (graphrag.py:185). The routing edge kinds — REALIZES /
HANDLES / LOADS / GUARDS — are deliberately ABSENT from that set, so a routing
node is UNREACHABLE by main recall even when a routing edge is incident on a
recalled node. These tests PIN that against future drift.

Same shared-graph discipline as the sibling recall tests (``test_recall_churn``):
each test layers its OWN routing nodes/edges (distinct ``rtreg-`` ids) onto the
session graph and tears them down in a ``finally``, so the graph other recall
tests share is left pristine.

Each test encodes one ac-7 clause:
  - test_routing_subgraph_not_traversed_by_default_recall — recall over a graph
    that ALSO contains routing nodes/edges returns byte-identical results to the
    routing-free baseline (a routing edge sits directly on the seed, yet is never
    traversed).
  - test_explicitly_requested_routing_relation_is_not_traversed — even when a
    caller EXPLICITLY asks to traverse REALIZES, the whitelist re-filter drops it,
    so no routing node is reached.
"""

from palimpsest.recall import recall

SVC_FILE = "src/main/java/kr/co/ecoletree/service/commute/service/CommuteService.java"
CTRL_FILE = "src/main/java/kr/co/ecoletree/service/commute/controller/CommuteController.java"

_ROUTE_ID = "rtreg-route"
_ENDPOINT_ID = "rtreg-endpoint"
_HOOK_ID = "rtreg-hook"
_FN_ID = "rtreg-handler-fn"

_ROUTING_KINDS = {"Route", "Endpoint", "Layout", "Hook"}


def _inject_routing(driver):
    """Layer a routing subgraph (all four routing node kinds + all four edge kinds)
    onto the shared graph. The keystone is a REALIZES edge from the recalled base
    File seed (SVC_FILE) directly onto a Route — a routing edge on the seed itself,
    which recall must still refuse to traverse. All ids carry the ``rtreg-`` prefix
    for safe teardown."""
    with driver.session() as s:
        s.run(
            "MERGE (n:Route {id:$id}) SET n.qualified_name=$id, n.name=$id, "
            "n.path='src/routes/x/+page.svelte', n.start_line=1, n.end_line=1",
            id=_ROUTE_ID,
        )
        s.run(
            "MERGE (n:Endpoint {id:$id}) SET n.qualified_name=$id, n.name=$id, "
            "n.path='src/routes/api/x/+server.ts', n.start_line=1, n.end_line=1",
            id=_ENDPOINT_ID,
        )
        s.run(
            "MERGE (n:Hook {id:$id}) SET n.qualified_name=$id, n.name=$id, "
            "n.path='src/hooks.server.ts', n.start_line=1, n.end_line=1",
            id=_HOOK_ID,
        )
        s.run(
            "MERGE (n:Function {id:$id}) SET n.qualified_name=$id, n.name='GET', "
            "n.path='src/routes/api/x/+server.ts', n.start_line=1, n.end_line=1",
            id=_FN_ID,
        )
        # REALIZES incident on the recalled seed File (keystone).
        s.run(
            "MATCH (f:File {id:$fid}) MATCH (r:Route {id:$rid}) "
            "MERGE (f)-[e:REALIZES]->(r) SET e.edge_kind='deterministic'",
            fid=SVC_FILE, rid=_ROUTE_ID,
        )
        # REALIZES incident on another base File — the endpoint.
        s.run(
            "MATCH (f:File {id:$fid}) MATCH (e2:Endpoint {id:$eid}) "
            "MERGE (f)-[e:REALIZES]->(e2) SET e.edge_kind='deterministic'",
            fid=CTRL_FILE, eid=_ENDPOINT_ID,
        )
        # The remaining three routing edge kinds, among the routing nodes.
        s.run(
            "MATCH (h:Hook {id:$hid}) MATCH (e2:Endpoint {id:$eid}) "
            "MERGE (h)-[e:GUARDS]->(e2) SET e.edge_kind='deterministic'",
            hid=_HOOK_ID, eid=_ENDPOINT_ID,
        )
        s.run(
            "MATCH (h:Hook {id:$hid}) MATCH (r:Route {id:$rid}) "
            "MERGE (h)-[e:GUARDS]->(r) SET e.edge_kind='deterministic'",
            hid=_HOOK_ID, rid=_ROUTE_ID,
        )
        s.run(
            "MATCH (fn:Function {id:$fid}) MATCH (e2:Endpoint {id:$eid}) "
            "MERGE (fn)-[e:HANDLES]->(e2) SET e.edge_kind='deterministic'",
            fid=_FN_ID, eid=_ENDPOINT_ID,
        )
        s.run(
            "MATCH (fn:Function {id:$fid}) MATCH (r:Route {id:$rid}) "
            "MERGE (fn)-[e:LOADS]->(r) SET e.edge_kind='deterministic'",
            fid=_FN_ID, rid=_ROUTE_ID,
        )


def _cleanup_routing(driver):
    with driver.session() as s:
        s.run("MATCH (n) WHERE n.id STARTS WITH 'rtreg-' DETACH DELETE n")


def test_routing_subgraph_not_traversed_by_default_recall(recall_db):
    """ac-7: recall over a graph that ALSO contains routing nodes/edges returns the
    SAME result as over the routing-free baseline — routing edges are outside
    DEFAULT_RELATIONS, so they are never walked."""
    driver = recall_db
    # Baseline over the pristine (routing-free) base fixture.
    base = recall(driver, SVC_FILE, depth=2, limit=100)
    try:
        _inject_routing(driver)

        # Non-vacuity: the Route IS a genuine graph neighbor of the seed via the
        # REALIZES edge — recall COULD reach it structurally, and refuses to.
        with driver.session() as s:
            adj = [
                r["id"]
                for r in s.run(
                    "MATCH (f:File {id:$id})-[:REALIZES]->(n) RETURN n.id AS id",
                    id=SVC_FILE,
                )
            ]
        assert _ROUTE_ID in adj

        mixed = recall(driver, SVC_FILE, depth=2, limit=100)

        # Byte-identical result: same items (ids, order, grounding) and same whole
        # payload — the routing layer is invisible to main recall.
        assert mixed["items"] == base["items"]
        assert mixed == base

        # And no routing node leaked into items by any path.
        mixed_ids = {it["id"] for it in mixed["items"]}
        assert _ROUTE_ID not in mixed_ids
        assert _ENDPOINT_ID not in mixed_ids
        assert all(it["kind"] not in _ROUTING_KINDS for it in mixed["items"])
    finally:
        _cleanup_routing(driver)


def test_explicitly_requested_routing_relation_is_not_traversed(recall_db):
    """ac-7: even when a caller EXPLICITLY requests REALIZES, ``_neighbors``
    re-filters the relation set to DEFAULT_RELATIONS (graphrag.py:185), so the
    routing edge on the seed is not traversed and no routing node is reached."""
    driver = recall_db
    try:
        _inject_routing(driver)
        out = recall(driver, SVC_FILE, depth=1, limit=100, relations=["REALIZES"])
        ids = {it["id"] for it in out["items"]}
        # REALIZES is dropped by the whitelist re-filter -> no traversable relation
        # survives, so only the seed remains and no routing node is reached.
        assert ids == {SVC_FILE}
        assert _ROUTE_ID not in ids
        assert all(it["kind"] not in _ROUTING_KINDS for it in out["items"])
    finally:
        _cleanup_routing(driver)
