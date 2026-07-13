"""SvelteKit routing recall channels: read-only, grounded, combinatorial queries
over the Route/Endpoint/Layout/Hook ontology (issue #9).

Separate, read-only entry points that sit ALONGSIDE :mod:`palimpsest.recall.graphrag`
and REUSE its public-enough assembly helpers (``_result`` / ``_item`` / ``_resolve``)
so every routing channel returns the SAME
``{items, sources, summaries, risks, decisions, relations, gaps, confidence,
expand_handle}`` shape as the rest of recall. No LLM anywhere — pure graph traversal
+ dict building.

Two honesty invariants are load-bearing here (both mirror graphrag's static-lower-bound
pattern, graphrag.py:1142-1150):

* **Guard soundness (ac-4).** A missing incoming ``GUARDS`` edge means "no
  *statically-detected* guard" — a server Layout or server Hook. It is NOT a verdict
  that the endpoint is unprotected: a runtime guard (``hooks.server`` ``handle`` logic,
  a ``load()`` redirect, a session/``locals`` check inside the handler) is invisible to
  static extraction and may still protect it. So ``recall_unguarded_endpoints`` ALWAYS
  carries the lower-bound disclosure in ``gaps`` — completeness is never claimed.

* **Bounded reachability (ac-5).** ``recall_endpoint_reachable`` is an ENDPOINT-DIRECTED
  per-hop bounded BFS with a NAMED per-frontier fan-out cap (mirrors
  ``recall_test_impact`` / ``recall_cochange``), NOT a variable-length
  ``[:CALLS*1..depth]`` path: a var-length path bounds only OUTPUT rows, not COMPUTE, so
  a hot handler would explode it. Each hop reads a server-bounded, id-ordered,
  per-node-fan-out-capped set of callees, so COMPUTE itself is bounded and the result is
  rebuild-deterministic. ``GUARDS`` is never traversed var-length from a Hook.

All values are parameterized (``$param``) — no id / url is ever string-interpolated into
Cypher. Label / relation names are the only literals and are closed ontology constants.
"""

from __future__ import annotations

from palimpsest.ir import CALLS, HANDLES, REALIZES, IMPORTS
from palimpsest.recall.graphrag import _result, _item, _resolve


# ── ac-4: unguarded endpoints (statically-detected-guard lower bound) ─────────

# ALWAYS emitted (ac-4 honesty): a missing incoming GUARDS edge is a LOWER BOUND on
# "protected". Static extraction only sees a server Layout (+layout.server) guarding a
# descendant page Route or a server Hook (hooks.server) guarding every Endpoint/Route —
# it CANNOT see a runtime guard (hooks.server handle logic, a load() redirect, a
# session/locals check inside the handler). So an endpoint with no GUARDS edge is "no
# statically-detected guard", NEVER a definitive "unprotected/public" verdict —
# completeness is not claimed.
_UNGUARDED_STATIC_LOWER_BOUND_GAP = (
    "a missing incoming GUARDS edge is a STATIC lower bound: it means no "
    "statically-detected guard (a server Layout / server Hook), NOT that the endpoint "
    "is unprotected — a runtime guard (hooks.server handle logic, a load() redirect, a "
    "session/locals check inside the handler) is invisible to static extraction and may "
    "still protect it; completeness is not claimed"
)

# Endpoints with NO incoming GUARDS edge. The absence is checked with a pattern
# predicate (NOT (ep)<-[:GUARDS]-()) — the endpoint's own null-guard set, id-ordered
# before LIMIT for rebuild-determinism. A whole node is never projected (author-omitted
# — mirrors graphrag): only the grounding properties surface via _item/_sources.
_UNGUARDED_ENDPOINTS = """
MATCH (ep:Endpoint)
WHERE NOT (ep)<-[:GUARDS]-()
RETURN ep.id AS id, labels(ep) AS labels, ep.name AS name,
       ep.qualified_name AS qualified_name,
       ep.path AS path, ep.start_line AS start_line, ep.end_line AS end_line,
       ep.source_commit AS source_commit, ep.committed_at AS committed_at
ORDER BY id
LIMIT $lim
"""


def recall_unguarded_endpoints(driver, limit=25):
    """Recall Endpoints with NO statically-detected guard (ac-4).

    A SEPARATE, read-only, global entry point: every ``Endpoint`` node carrying no
    incoming ``GUARDS`` edge, id-ordered and BOUNDED by ``limit``. Returns the standard
    ``{items, sources, ...}`` shape; each item is a grounded Endpoint (author-omitted).

    Soundness (ac-4): the result ALWAYS carries the lower-bound disclosure in ``gaps`` —
    a missing GUARDS edge is "no statically-detected guard", never a definitive
    "unprotected" verdict (a runtime guard may still exist). Combinatorial only.
    """
    gaps = [_UNGUARDED_STATIC_LOWER_BOUND_GAP]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_UNGUARDED_ENDPOINTS, lim=limit)]
    items = [_item(rec, None, 1) for rec in rows]
    return _result(items, gaps, None, [])


# ── ac-5: endpoint-reachable code (bounded, endpoint-directed BFS) ────────────

# ALWAYS emitted (ac-5 honesty): static CALLS reachability is a LOWER BOUND. A wrapped
# handler (export const GET = protect(async () => {})) emits no handler FUNCTION, and
# reflective / dynamic-dispatch calls are invisible — so an empty or short result does
# NOT mean "the endpoint reaches nothing"; completeness is not claimed.
_REACHABLE_STATIC_LOWER_BOUND_GAP = (
    "static CALLS reachability is a lower bound: a wrapped handler and reflective / "
    "dynamic-dispatch calls are invisible to it, so an empty or short result does NOT "
    "mean 'the endpoint reaches no further code' — completeness is not claimed"
)

# Per-hop fan-out cap: a single high-degree handler / callee may call a huge number of
# functions; this bounds the callees ONE frontier node contributes per hop so a hot
# handler never explodes the forward traversal (mirrors _TEST_IMPACT_FANOUT_CAP /
# _COCHANGE_FANOUT_CAP — a threshold that must exist gets a named constant, never a
# magic literal in the query).
_REACHABLE_FANOUT_CAP = 512

# Endpoint -> its handler Function(s): the INCOMING HANDLES edge (handler -[:HANDLES]->
# endpoint). Per-endpoint fan-out capped, id-ordered. A whole node is never projected.
_ENDPOINT_HANDLERS = """
MATCH (ep:Endpoint {id: $id})<-[:HANDLES]-(h)
RETURN DISTINCT h.id AS id, labels(h) AS labels, h.name AS name,
       h.qualified_name AS qualified_name,
       h.path AS path, h.start_line AS start_line, h.end_line AS end_line,
       h.source_commit AS source_commit, h.committed_at AS committed_at
ORDER BY id
LIMIT $fanout
"""

# One FORWARD-CALLS hop: ALL callees of a frontier of Functions (caller -[:CALLS]->
# callee). Per-frontier-node fan-out capped server-side; DISTINCT dedups a callee reached
# from several frontier nodes; id-ordered before LIMIT for rebuild-determinism. GUARDS is
# never walked here — only CALLS, and only forward from the handler.
_REACHABLE_CALLEES = """
UNWIND $ids AS sid
CALL {
    WITH sid
    MATCH (caller {id: sid})-[:CALLS]->(callee)
    RETURN callee ORDER BY callee.id LIMIT $fanout
}
RETURN DISTINCT callee.id AS id, labels(callee) AS labels, callee.name AS name,
       callee.qualified_name AS qualified_name,
       callee.path AS path, callee.start_line AS start_line, callee.end_line AS end_line,
       callee.source_commit AS source_commit, callee.committed_at AS committed_at
ORDER BY id
LIMIT $lim
"""


def _endpoint_handlers(driver, endpoint_id):
    """The handler Function(s) of an Endpoint via incoming HANDLES. Rows only;
    projection is the caller's job via _item/_sources. Per-endpoint fan-out is capped."""
    with driver.session() as session:
        rows = session.run(
            _ENDPOINT_HANDLERS, id=endpoint_id, fanout=_REACHABLE_FANOUT_CAP
        )
        return [r.data() for r in rows]


def _reachable_hop(driver, frontier, visited, depth, budget):
    """One FORWARD-CALLS BFS hop. Emits up to ``budget`` new callee items (deterministic
    id order); every new callee also advances the frontier. Returns
    (items, emitted_ids, truncated). Mirrors :func:`_test_impact_hop`: the server read is
    bounded to ``budget + |visited| + 1`` rows, per-node fan-out capped."""
    items, emitted = [], []
    truncated = False
    read_limit = budget + len(visited) + 1
    with driver.session() as session:
        rows = [
            r.data()
            for r in session.run(
                _REACHABLE_CALLEES,
                ids=list(frontier),
                lim=read_limit,
                fanout=_REACHABLE_FANOUT_CAP,
            )
        ]
    for rec in rows:
        nid = rec["id"]
        if nid in visited:
            continue
        if len(items) >= budget:
            truncated = True
            break
        visited.add(nid)
        emitted.append(nid)
        items.append(_item(rec, CALLS, depth))
    return items, emitted, truncated


def recall_endpoint_reachable(driver, endpoint_id, depth=10, limit=25):
    """Recall the code an Endpoint reaches: its handler Function and that handler's
    transitive CALLS callees (ac-5).

    A SEPARATE, read-only entry point. From the ``Endpoint`` node (``endpoint_id``),
    follow the INCOMING ``HANDLES`` edge to the handler Function(s) (depth 1,
    ``relation=HANDLES``), then walk ``CALLS`` FORWARD hop by hop to the functions the
    handler transitively reaches (depth 2+, ``relation=CALLS``). Same bounded
    ``{items, sources, ...}`` shape as the sibling channels.

    Bounding (ac-5 — COMPUTE, not just output rows): an ENDPOINT-DIRECTED per-hop bounded
    BFS (mirrors ``recall_test_impact``), NOT a variable-length ``[:CALLS*1..depth]``
    path. Each hop reads at most ``budget + |visited| + 1`` server-side rows,
    per-frontier-node fan-out is capped at ``_REACHABLE_FANOUT_CAP``, total items are
    bounded by ``limit``, and callees are id-ordered before LIMIT for
    rebuild-determinism — so a hot handler never explodes. ``depth`` is the transitive-hop
    ceiling; ``depth <= 1`` degrades to a handlers-only result.

    Gaps (ac-5 honesty): the static-lower-bound note is ALWAYS present (static CALLS
    reachability misses wrapped handlers / reflective dispatch); an unresolved endpoint,
    or an endpoint with no HANDLES handler, is stated as an explicit gap. Completeness is
    never claimed. Combinatorial only, no LLM.
    """
    gaps = [_REACHABLE_STATIC_LOWER_BOUND_GAP]

    seed = _resolve(driver, endpoint_id)
    if seed is None:
        gaps.append(f"endpoint '{endpoint_id}' did not resolve to any node in the graph")
        return _result([], gaps, None, [])

    handlers = _endpoint_handlers(driver, endpoint_id)
    if not handlers:
        gaps.append(
            f"endpoint '{endpoint_id}' has no HANDLES handler Function — a wrapped "
            "handler (export const GET = protect(...)) emits no handler FUNCTION, so its "
            "reachable code is invisible to static extraction"
        )
        return _result([], gaps, None, [])

    items = []
    visited = {seed["id"]}
    frontier = []
    for rec in handlers:
        if len(items) >= limit:
            break
        nid = rec["id"]
        if nid in visited:
            continue
        visited.add(nid)
        frontier.append(nid)
        items.append(_item(rec, HANDLES, 1))  # the handler, reached via HANDLES

    cur_depth = 1
    while frontier and cur_depth < depth and len(items) < limit:
        cur_depth += 1
        new_items, emitted, truncated = _reachable_hop(
            driver, frontier, visited, cur_depth, limit - len(items)
        )
        items.extend(new_items)
        if truncated:
            break
        frontier = emitted  # next hop expands from the callees just found

    return _result(items, gaps, None, [])


# ── URL <-> file: REALIZES round-trip ─────────────────────────────────────────

# The File(s) that REALIZE a Route (Route <-[:REALIZES]- File). URL -> file.
_ROUTE_FILES = """
MATCH (r:Route {id: $url})<-[:REALIZES]-(f:File)
RETURN DISTINCT f.id AS id, labels(f) AS labels, f.name AS name,
       f.qualified_name AS qualified_name,
       f.path AS path, f.start_line AS start_line, f.end_line AS end_line,
       f.source_commit AS source_commit, f.committed_at AS committed_at
ORDER BY id
LIMIT $lim
"""

# The Route(s) a File REALIZES (File -[:REALIZES]-> Route). file -> URL.
_FILE_ROUTE = """
MATCH (f:File {id: $file_id})-[:REALIZES]->(r:Route)
RETURN DISTINCT r.id AS id, labels(r) AS labels, r.name AS name,
       r.qualified_name AS qualified_name,
       r.path AS path, r.start_line AS start_line, r.end_line AS end_line,
       r.source_commit AS source_commit, r.committed_at AS committed_at
ORDER BY id
LIMIT $lim
"""


def recall_route_files(driver, url, limit=25):
    """Recall the File(s) that REALIZE a Route (URL -> file).

    A SEPARATE, read-only entry point over the ``REALIZES`` spine: the ``+page.*`` /
    ``+error.*`` files defining the page Route at ``url``, id-ordered and BOUNDED by
    ``limit``. An unresolved / route-less URL is an explicit gap. Combinatorial only.
    """
    with driver.session() as session:
        rows = [r.data() for r in session.run(_ROUTE_FILES, url=url, lim=limit)]
    items = [_item(rec, REALIZES, 1) for rec in rows]
    gaps = [] if items else [f"url '{url}' resolves to no Route with REALIZES files"]
    return _result(items, gaps, None, [])


def recall_file_route(driver, file_id, limit=25):
    """Recall the Route(s) a File REALIZES (file -> URL).

    The inverse of :func:`recall_route_files` over the same ``REALIZES`` spine: the page
    Route(s) a routing File defines, id-ordered and BOUNDED by ``limit``. A file that
    realizes no Route (an ordinary component / util, or a ``+server`` realizing only an
    Endpoint) is an explicit gap. Combinatorial only.
    """
    with driver.session() as session:
        rows = [r.data() for r in session.run(_FILE_ROUTE, file_id=file_id, lim=limit)]
    items = [_item(rec, REALIZES, 1) for rec in rows]
    gaps = [] if items else [f"file '{file_id}' REALIZES no Route"]
    return _result(items, gaps, None, [])


# ── ac-6: universal code importing a server-only module ───────────────────────

# An IMPORTS edge whose dst File is server_only=true while the src File is NOT
# server-only (server_only IS NULL): universal (client-shippable) code importing a
# server-only module — a leak of server-only code into the universal bundle. Both
# endpoints id-ordered; a whole node is never projected. ``server_only IS NULL`` matches
# a universal File (the marker is absent / null, never phantom-false).
_SERVER_ONLY_IMPORTS = """
MATCH (s:File)-[:IMPORTS]->(d:File)
WHERE d.server_only = true AND s.server_only IS NULL
RETURN DISTINCT s.id AS id, labels(s) AS labels, s.name AS name,
       s.qualified_name AS qualified_name,
       s.path AS path, s.start_line AS start_line, s.end_line AS end_line,
       s.source_commit AS source_commit, s.committed_at AS committed_at,
       d.id AS server_only_id, d.path AS server_only_path
ORDER BY id, server_only_id
LIMIT $lim
"""


def recall_server_only_imports(driver, limit=25):
    """Recall universal Files that IMPORT a server-only module (ac-6).

    A SEPARATE, read-only, global entry point: every ``IMPORTS`` edge whose destination
    File is ``server_only=true`` (a ``.server.`` / ``+server`` / ``src/lib/server``
    module) while the source File is NOT server-only (``server_only IS NULL``) —
    universal code pulling a server-only module into the client bundle. id-ordered and
    BOUNDED by ``limit``. Each item is the grounded importing (universal) File carrying
    a ``server_only_module`` = ``{id, path}`` of the server-only target it imports.
    Combinatorial only.
    """
    with driver.session() as session:
        rows = [r.data() for r in session.run(_SERVER_ONLY_IMPORTS, lim=limit)]
    items = []
    for rec in rows:
        it = _item(rec, IMPORTS, 1)
        it["server_only_module"] = {
            "id": rec["server_only_id"],
            "path": rec["server_only_path"],
        }
        items.append(it)
    gaps = [] if items else ["no universal File imports a server-only module"]
    return _result(items, gaps, None, [])
