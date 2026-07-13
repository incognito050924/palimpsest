"""wi_260713c7t (n-calls-api-matcher-recall) — the cross-tier CALLS_API integration.

WHY THESE TESTS EXIST
  ac-5 (true positive, the cross-tier BRIDGE): a SYNTHETIC PAIRED corpus — a front-end
    ``fetch('/api/orders')`` and a back-end Spring ``@GetMapping("/api/orders")`` — run
    through the REAL pipeline (``dispatch`` -> matcher -> loader) must yield at least ONE
    CALLS_API edge from the f/e ApiCall to the ``spring:`` Endpoint, marked
    ``edge_kind='inferred'`` and carrying a confidence + ``matched_route`` grounding. This
    pins Decision 4: the two tiers, seen as peers of the SAME canonical route, are linked.
  ac-6 (disclosed gap, absence != endpoint-unused): a dynamic-URL call (``fetch(someVar)``)
    emits NO ApiCall and so produces NO CALLS_API edge; AND the recall channel over an
    Endpoint with no caller ALWAYS discloses the static-lower-bound gap — an absent edge is
    "no statically-linked caller found", never "endpoint unused" (Frozen Invariant 6).

Scope-local MOCK-UNIT: the matcher is pure (no DB); the loader + recall are exercised
against an in-memory fake Neo4j driver that answers the loader's node queries + the recall
traversals and MERGE-dedups edges on ``(src, dst)``. No testcontainer, no network — so the
per-node run is a valid early hint independent of sibling/unbuilt infra.
"""

from pathlib import Path

from palimpsest.extract import dispatch
from palimpsest.extract.calls_api import RouteEnd, match_calls
from palimpsest.ir import API_CALL, ENDPOINT, Provenance
from palimpsest.kg.calls_api import load_calls_api
from palimpsest.recall.api_links import recall_call_endpoints, recall_endpoint_callers

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

APICALL_ID = "apicall:GET /api/orders"
SPRING_EP_ID = "spring:GET /api/orders"

# A modern @RestController whose @GetMapping route matches the f/e fetch literally.
_ORDER_CONTROLLER = (
    "package p;\n"
    "import org.springframework.web.bind.annotation.RestController;\n"
    "import org.springframework.web.bind.annotation.GetMapping;\n"
    "@RestController\n"
    "public class OrderController {\n"
    '  @GetMapping("/api/orders")\n'
    '  public String list() { return "x"; }\n'
    "}\n"
)

# Front-end / back-end pair that share the /api/orders route.
PAIRED = {
    "web/orders.js": "fetch('/api/orders');\n",
    "OrderController.java": _ORDER_CONTROLLER,
}

# The same back end, but the caller's URL is a bare variable -> non-templatable dynamic
# (ac-6): no ApiCall node, so no CALLS_API edge.
DYNAMIC = {
    "web/dyn.js": "fetch(someVar);\n",
    "OrderController.java": _ORDER_CONTROLLER,
}


def _write(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)


def _route_end(node) -> RouteEnd:
    return RouteEnd(
        id=node.id,
        qualified_name=node.qualified_name,
        committed_at=node.provenance.committed_at,
        source_commit=node.provenance.source_commit,
    )


def _row(node) -> dict:
    """An IR Node projected to the graph-row shape the loader / recall queries return."""
    return {
        "id": node.id,
        "qualified_name": node.qualified_name,
        "name": node.name,
        "labels": [node.kind],
        "path": node.path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "committed_at": node.provenance.committed_at,
        "source_commit": node.provenance.source_commit,
        # Mirrors ingest ``_node_row`` -> ``_NODE_MERGE``: the dataflow-recovery marker
        # persisted on the ApiCall node and read back by the loader (ac-4).
        "dataflow_derived": node.dataflow_derived,
    }


# --- in-memory fake Neo4j driver (mock-unit; MERGE-dedups on (src, dst)) --------------


class _Rec:
    def __init__(self, d):
        self._d = d

    def __getitem__(self, k):
        return self._d[k]

    def data(self):
        return dict(self._d)


class _Result:
    def __init__(self, rows):
        self._rows = [_Rec(r) for r in rows]

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, drv):
        self._d = drv

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **p):
        d = self._d
        if "MERGE (a)-[r:CALLS_API]->(e)" in query:
            d.merged[(p["src"], p["dst"])] = dict(p)  # MERGE-on-(src,dst): idempotent
            return _Result([])
        if "[r:CALLS_API]->(ep:Endpoint {id: $id})" in query:  # endpoint -> callers
            rows = [
                {**d.apicall_by_id[src], **d.merged[(src, dst)]}
                for (src, dst) in sorted(d.merged) if dst == p["id"]
            ]
            return _Result(rows)
        if "(a:ApiCall {id: $id})-[r:CALLS_API]" in query:  # call -> endpoints
            rows = [
                {**d.endpoint_by_id[dst], **d.merged[(src, dst)]}
                for (src, dst) in sorted(d.merged) if src == p["id"]
            ]
            return _Result(rows)
        if "MATCH (a:ApiCall)" in query:
            return _Result(sorted(d.apicalls, key=lambda r: r["id"]))
        if "MATCH (e:Endpoint)" in query:
            return _Result(sorted(d.endpoints, key=lambda r: r["id"]))
        raise AssertionError(f"unexpected query: {query[:70]!r}")


class _FakeDriver:
    def __init__(self, apicalls, endpoints):
        self.apicalls = list(apicalls)
        self.endpoints = list(endpoints)
        self.apicall_by_id = {r["id"]: r for r in self.apicalls}
        self.endpoint_by_id = {r["id"]: r for r in self.endpoints}
        self.merged = {}

    def session(self, *a, **k):
        return _Session(self)


def _dispatch(tmp_path, files):
    _write(tmp_path, files)
    return dispatch(tmp_path, PROV, repo_name="paired")


# --- ac-5: the true-positive cross-tier bridge ---------------------------------------


def test_ac5_dispatch_yields_paired_nodes(tmp_path):
    """Precondition the bridge depends on: dispatch over the paired corpus really does
    extract BOTH the f/e ApiCall and the b/e spring: Endpoint of /api/orders."""
    ir = _dispatch(tmp_path, PAIRED)
    assert ir.node(APICALL_ID) is not None, "f/e fetch should produce an ApiCall node"
    assert ir.node(SPRING_EP_ID) is not None, "Spring @GetMapping should produce an Endpoint"


def test_ac5_matcher_links_fe_call_to_spring_endpoint(tmp_path):
    """ac-5 (matcher output shape): the pure matcher pairs the f/e ApiCall to the b/e
    spring: Endpoint with confidence 1.0 (exact literal, single candidate) and a
    matched_route grounding."""
    ir = _dispatch(tmp_path, PAIRED)
    calls = [_route_end(n) for n in ir.nodes_of(API_CALL)]
    endpoints = [_route_end(n) for n in ir.nodes_of(ENDPOINT)]

    matches = match_calls(calls, endpoints)
    bridge = [m for m in matches if m.source_id == APICALL_ID and m.target_id == SPRING_EP_ID]
    assert len(bridge) == 1, f"expected exactly one f/e->b/e link, got {matches}"
    m = bridge[0]
    assert m.confidence == 1.0            # exact method + literal path, single candidate
    assert m.candidate_count == 1
    assert m.matched_route == "GET /api/orders"
    assert m.code_bound_at == PROV.committed_at   # freshness follows the calling code
    assert m.source_commit == PROV.source_commit


def test_ac5_loader_merges_inferred_edge(tmp_path):
    """ac-5 (loader): running the dedicated loader over the graph MERGEs the CALLS_API
    edge with edge_kind='inferred' + confidence + matched_route + provenance grounding."""
    ir = _dispatch(tmp_path, PAIRED)
    drv = _FakeDriver(
        apicalls=[_row(n) for n in ir.nodes_of(API_CALL)],
        endpoints=[_row(n) for n in ir.nodes_of(ENDPOINT)],
    )
    res = load_calls_api(drv)
    assert res.loaded >= 1
    assert (APICALL_ID, SPRING_EP_ID) in drv.merged
    e = drv.merged[(APICALL_ID, SPRING_EP_ID)]
    assert e["edge_kind"] == "inferred"          # NOT deterministic (Frozen Invariant 3)
    assert e["confidence"] == 1.0
    assert e["matched_route"] == "GET /api/orders"
    assert e["candidate_count"] == 1
    assert e["generator"] == "palimpsest"
    assert e["model"] == "cross-tier-route-matcher/v1"
    assert e["source_commit"] == PROV.source_commit
    assert e["code_bound_at"] == PROV.committed_at


def test_ac5_loader_is_idempotent(tmp_path):
    """Re-running the loader over an unchanged graph merges the SAME single edge
    (MERGE-on-(src,dst)) — a rebuildable projection, no duplicate."""
    ir = _dispatch(tmp_path, PAIRED)
    drv = _FakeDriver(
        apicalls=[_row(n) for n in ir.nodes_of(API_CALL)],
        endpoints=[_row(n) for n in ir.nodes_of(ENDPOINT)],
    )
    load_calls_api(drv)
    load_calls_api(drv)
    keys = [k for k in drv.merged if k == (APICALL_ID, SPRING_EP_ID)]
    assert len(keys) == 1


def test_ac5_recall_endpoint_callers_returns_the_call(tmp_path):
    """ac-5 (recall positive): given the endpoint, the recall channel surfaces the f/e
    ApiCall caller, carrying the link's confidence + matched_route on ``link``."""
    ir = _dispatch(tmp_path, PAIRED)
    drv = _FakeDriver(
        apicalls=[_row(n) for n in ir.nodes_of(API_CALL)],
        endpoints=[_row(n) for n in ir.nodes_of(ENDPOINT)],
    )
    load_calls_api(drv)
    res = recall_endpoint_callers(drv, SPRING_EP_ID)
    ids = [it["id"] for it in res["items"]]
    assert APICALL_ID in ids
    it = next(it for it in res["items"] if it["id"] == APICALL_ID)
    assert it["link"]["confidence"] == 1.0
    assert it["link"]["matched_route"] == "GET /api/orders"


# --- ac-6: dynamic URL is a disclosed gap, never "endpoint unused" --------------------


def test_ac6_dynamic_url_yields_no_apicall_and_no_edge(tmp_path):
    """ac-6: a bare-variable URL emits no ApiCall, so the matcher + loader produce NO
    CALLS_API edge to the (real) Spring endpoint — an honest absence, not a false link."""
    ir = _dispatch(tmp_path, DYNAMIC)
    assert ir.nodes_of(API_CALL) == [], "dynamic fetch(var) must emit no ApiCall"
    assert ir.node(SPRING_EP_ID) is not None, "the endpoint still exists (it is not 'unused')"

    drv = _FakeDriver(
        apicalls=[_row(n) for n in ir.nodes_of(API_CALL)],
        endpoints=[_row(n) for n in ir.nodes_of(ENDPOINT)],
    )
    res = load_calls_api(drv)
    assert res.loaded == 0
    assert drv.merged == {}


def test_ac6_recall_discloses_static_lower_bound_gap(tmp_path):
    """ac-6: over an Endpoint with no caller, recall returns NO items but ALWAYS discloses
    the static-lower-bound gap — absence is 'no statically-linked caller', not 'unused'."""
    ir = _dispatch(tmp_path, DYNAMIC)
    drv = _FakeDriver(
        apicalls=[_row(n) for n in ir.nodes_of(API_CALL)],
        endpoints=[_row(n) for n in ir.nodes_of(ENDPOINT)],
    )
    res = recall_endpoint_callers(drv, SPRING_EP_ID)
    assert res["items"] == []
    assert res["gaps"], "the lower-bound disclosure must always be present"
    disclosure = " ".join(res["gaps"]).lower()
    assert "lower bound" in disclosure
    assert "unused" in disclosure  # explicitly refutes the "endpoint unused" reading

    # The inverse channel carries the same disclosure even for an unknown call id.
    res2 = recall_call_endpoints(drv, "apicall:GET /nope")
    assert res2["items"] == []
    assert any("lower bound" in g.lower() for g in res2["gaps"])


# --- wi_260713iah: host-separation + transform-provenance + confidence cap ------------
#
# WHY THESE EXIST (background for approver + consumer):
#   mechanism B grounds an S2S call by stamping the RESOLVED target host into the ApiCall
#   qualified_name (``apicall:GET http://svc:8080/api/x``). That host BINDS which service
#   is called, but it is NOT part of the route: the peer Endpoint is host-less
#   (``spring:GET /api/x``). So the matcher MUST reduce a grounded ApiCall to method+PATH
#   (host stripped) to match, and use the host only for target-binding + confidence.
#   And a link whose route was DERIVED (host-grounded / one-hop dataflow / proxy-rewritten)
#   is NOT a plain literal direct-match — it can never carry the literal 1.0 tier, and a
#   non-unique (>1 candidate) derived resolution is capped harder still.

from palimpsest.extract.provenance import LITERAL, CONFIG, DATAFLOW, DERIVED_CAP, AMBIGUOUS_CAP


def _re(qn, transform=LITERAL):
    return RouteEnd(id=qn, qualified_name=qn, transform=transform)


def test_grounded_apicall_host_stripped_matches_endpoint():
    """part 3: a host-grounded ApiCall reduces to method+PATH and matches the host-less
    Spring Endpoint; the host is surfaced as ``target_host`` (binding), not the route."""
    call = _re("apicall:GET http://portal:8080/api/v1/connectors/{id}")
    ep = _re("spring:GET /api/v1/connectors/{userId}")
    matches = match_calls([call], [ep])
    assert len(matches) == 1
    m = matches[0]
    assert m.matched_route == "GET /api/v1/connectors/{}"   # host is NOT in the match key
    assert m.target_host == "http://portal:8080"            # host bound as target metadata
    assert m.transform == CONFIG


def test_config_derived_confidence_below_literal():
    """part 2: a grounded (config-derived) link is capped BELOW a literal exact match —
    a derived route is never stamped with the literal 1.0 tier."""
    lit_m = match_calls([_re("apicall:GET /api/orders")], [_re("spring:GET /api/orders")])[0]
    gr_m = match_calls(
        [_re("apicall:GET http://svc:8080/api/orders")], [_re("spring:GET /api/orders")]
    )[0]
    assert lit_m.confidence == 1.0            # literal exact, single candidate
    assert lit_m.transform == LITERAL
    assert gr_m.transform == CONFIG
    assert gr_m.confidence == DERIVED_CAP     # derived, provably unique -> capped, not 1.0
    assert gr_m.confidence < lit_m.confidence


def test_multi_candidate_derived_capped_harder():
    """part 2: a derived link whose resolution is NOT provably unique (>1 matching
    Endpoint context) is capped harder than a unique derived link."""
    grounded = _re("apicall:GET http://svc:8080/api/x")
    matches = match_calls([grounded], [_re("spring:GET /api/x"), _re("GET /api/x")])
    assert len(matches) == 2
    for m in matches:
        assert m.candidate_count == 2
        assert m.transform == CONFIG
        assert m.confidence == AMBIGUOUS_CAP   # derived + non-unique -> weakest tier


def test_dataflow_transform_discounted():
    """part 2: a one-hop dataflow-recovered call (carried as an explicit transform) is
    derived, so its link is discounted below a literal direct-match."""
    call = _re("apicall:GET /api/v1/connectors/{}", transform=DATAFLOW)
    ep = _re("spring:GET /api/v1/connectors/{id}")
    m = match_calls([call], [ep])[0]
    assert m.transform == DATAFLOW
    assert m.confidence < 1.0


def test_loader_writes_transform_and_target_host(tmp_path):
    """part 2/3: the inferred edge carries the transform provenance + bound target_host so
    a derived link is auditably distinct from a literal one on the graph."""
    drv = _FakeDriver(
        apicalls=[
            {
                "id": "apicall:GET http://svc:8080/api/orders",
                "qualified_name": "apicall:GET http://svc:8080/api/orders",
                "committed_at": PROV.committed_at,
                "source_commit": PROV.source_commit,
            }
        ],
        endpoints=[
            {
                "id": SPRING_EP_ID,
                "qualified_name": SPRING_EP_ID,
                "committed_at": PROV.committed_at,
                "source_commit": PROV.source_commit,
            }
        ],
    )
    load_calls_api(drv)
    e = drv.merged[("apicall:GET http://svc:8080/api/orders", SPRING_EP_ID)]
    assert e["transform"] == CONFIG
    assert e["target_host"] == "http://svc:8080"
    assert e["confidence"] == DERIVED_CAP


def _load_single_edge(apicall_row, endpoint_row):
    """Run the loader over ONE ApiCall + ONE Endpoint and return the single merged edge."""
    drv = _FakeDriver(apicalls=[apicall_row], endpoints=[endpoint_row])
    load_calls_api(drv)
    (edge,) = drv.merged.values()
    return edge


def test_dataflow_derived_literal_path_capped_below_genuine_literal():
    """ac-4 regression (real boxwood): the engine->portal GET /api/v1/connectors/search
    link was RECOVERED by one-hop param->uri dataflow, yet landed at conf 1.0 /
    transform='literal' — indistinguishable from a direct-literal match. The route is
    NON-templated, so the templated rung does not discount it; only the dataflow marker
    (persisted on the ApiCall node, read back by the loader) can. A dataflow-recovered
    literal path must read transform='dataflow' at <1.0, while a GENUINE direct-literal
    call to the SAME path stays transform='literal' at 1.0."""
    path = "/api/v1/connectors/search"       # non-templated: no {} placeholder
    ep_id = f"spring:GET {path}"
    ep_row = {"id": ep_id, "qualified_name": ep_id,
              "committed_at": PROV.committed_at, "source_commit": PROV.source_commit}
    call_id = f"apicall:GET {path}"
    base_call = {"id": call_id, "qualified_name": call_id,
                 "committed_at": PROV.committed_at, "source_commit": PROV.source_commit}

    df_edge = _load_single_edge({**base_call, "dataflow_derived": True}, ep_row)
    lit_edge = _load_single_edge({**base_call, "dataflow_derived": None}, ep_row)

    # The dataflow-recovered link is auditably derived and never reaches the literal 1.0.
    assert df_edge["transform"] == DATAFLOW
    assert df_edge["confidence"] < 1.0
    assert df_edge["confidence"] == DERIVED_CAP    # provably unique derived -> 0.7
    # A genuine direct-literal call to the SAME non-templated path is untouched (#21).
    assert lit_edge["transform"] == LITERAL
    assert lit_edge["confidence"] == 1.0


# --- package surface ------------------------------------------------------------------


def test_recall_channels_exported_from_package():
    from palimpsest.recall import recall_call_endpoints as pkg_call
    from palimpsest.recall import recall_endpoint_callers as pkg_ep

    assert pkg_ep is recall_endpoint_callers
    assert pkg_call is recall_call_endpoints
