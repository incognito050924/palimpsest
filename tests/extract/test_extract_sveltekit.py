"""TDD for the SvelteKit route-detection extractor pass (path/filename detection
over the already-extracted IR nodes).

WHY these tests exist:
  A SvelteKit route is NOT a tree-sitter symbol — it is a FILESYSTEM convention:
  ``+``-prefixed files under ``src/routes/`` (and ``src/hooks.*``) define
  Route/Endpoint/Layout/Hook nodes whose identity is a normalized URL. The pass runs
  INSIDE ``extract_ecmascript`` AFTER the ts/js/svelte fragments are built, promoting
  those files into the routing ontology and wiring REALIZES/HANDLES/LOADS/GUARDS over
  the already-extracted FILE + handler FUNCTION nodes. Every test drives the REAL
  ``dispatch()`` entry so the interception is proven WIRED, not tested in isolation.

What each AC pins:
  * ac-1: ONLY ``+``-prefixed files are promoted; a co-located ``Card.svelte`` and any
    ``src/lib`` file carry NO incoming REALIZES (they are not routes).
  * ac-2: each ``+server`` HTTP handler -> an Endpoint (HANDLES from the handler
    FUNCTION); a ``+page.server`` ``load`` -> a LOADS edge onto its dir's Route.
  * ac-3: a Route id is the normalized URL — a ``(group)`` segment is stripped,
    ``[slug]`` kept, and ``[id=integer]`` vs ``[id=hex]`` stay DISTINCT sibling ids
    (the matcher is NOT stripped — stripping would collide two valid routes under
    MERGE). Extraction is idempotent (identical node/edge id sets across two runs).
  * ac-4: GUARDS inheritance — a server Layout (``+layout.server.ts``) guards its
    DESCENDANT page Route but NEVER an Endpoint (a ``+server`` bypasses layout load,
    the keystone); a server Hook (``hooks.server.ts``) guards EVERY Endpoint AND page
    Route.
  * ac-6: ``server_only`` is True on ``.server`` / ``+server`` / ``src/lib/server``
    files, None on a universal component / util (a pure post-mutation PROPERTY).
"""

from pathlib import Path

from palimpsest.ir import (
    Provenance,
    FILE,
    ROUTE,
    ENDPOINT,
    LAYOUT,
    HOOK,
    REALIZES,
    HANDLES,
    LOADS,
    GUARDS,
)
from palimpsest.extract import dispatch

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

FIXTURE = Path(__file__).parent / "fixtures" / "sveltekit_app"

# A minimal hooks.server variant. It lives in tmp_path (built inline) rather than in
# the main fixture app because the presence of hooks.server.ts flips GLOBAL guard
# behavior (it guards EVERY endpoint + route) — folding it into sveltekit_app would
# invalidate the layout-only unguarded-endpoint case (ac-4). Same tmp_path pattern as
# test_extract_svelte.py / test_dispatch.py.
HOOKED_APP = {
    "src/hooks.server.ts": (
        "export const handle = async ({ event, resolve }) => {\n"
        "  return resolve(event);\n"
        "};\n"
    ),
    "src/routes/+page.svelte": "<h1>home</h1>\n",
    "src/routes/secure/+server.ts": (
        'export function GET() {\n  return new Response("secret");\n}\n'
    ),
}


def _ir():
    """Drive the REAL production dispatch entry over the fixture app (ac-1 wiring)."""
    return dispatch(FIXTURE, PROV, repo_name="app")


def _ir_hooked(tmp_path):
    for rel, src in HOOKED_APP.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return dispatch(tmp_path, PROV, repo_name="hooked")


# --- ac-1: ONLY +-prefixed files under src/routes are promoted ---


def test_ac1_only_plus_prefixed_files_promoted():
    ir = _ir()
    route_qns = {n.qualified_name for n in ir.nodes_of(ROUTE)}
    # +page.svelte dirs ARE promoted (positive control).
    assert "/" in route_qns
    assert "/blog/[slug]" in route_qns

    # a co-located Card.svelte and src/lib files carry NO incoming REALIZES: they are
    # ordinary components/util, not routing nodes.
    realizes_srcs = {e.src for e in ir.edges_of(REALIZES)}
    assert "src/routes/blog/Card.svelte" not in realizes_srcs
    assert "src/lib/util.ts" not in realizes_srcs
    assert "src/lib/server/secrets.ts" not in realizes_srcs

    # and neither surfaces as a routing node of any kind.
    routing_qns = {
        n.qualified_name
        for n in ir.nodes
        if n.kind in (ROUTE, ENDPOINT, LAYOUT, HOOK)
    }
    assert "src/routes/blog/Card.svelte" not in routing_qns
    assert "src/lib/util.ts" not in routing_qns

    # positive: the root +page.svelte REALIZES its Route (interception WIRED via dispatch).
    assert ir.has_edge(REALIZES, "src/routes/+page.svelte", "/")


# --- ac-2: +server HTTP handlers -> Endpoints (HANDLES); +page.server load -> LOADS ---


def test_ac2_server_handlers_and_page_load():
    ir = _ir()
    ep_qns = {n.qualified_name for n in ir.nodes_of(ENDPOINT)}
    # one Endpoint PER HTTP method present in the +server file.
    assert "GET /api/users/[id]" in ep_qns
    assert "POST /api/users/[id]" in ep_qns

    # HANDLES: the handler FUNCTION (path == +server file AND name == method) -> Endpoint.
    assert ir.has_edge(
        HANDLES,
        "src/routes/api/users/[id]/+server.ts.GET()",
        "GET /api/users/[id]",
    )
    assert ir.has_edge(
        HANDLES,
        "src/routes/api/users/[id]/+server.ts.POST()",
        "POST /api/users/[id]",
    )

    # LOADS: the +page.server `load` FUNCTION -> the dir's Route.
    assert ir.has_edge(
        LOADS,
        "src/routes/blog/[slug]/+page.server.ts.load()",
        "/blog/[slug]",
    )


# --- ac-3: URL normalization + matcher-sibling distinctness + idempotency ---


def test_ac3_url_normalization_group_param_matcher():
    ir = _ir()
    route_qns = {n.qualified_name for n in ir.nodes_of(ROUTE)}

    # a (marketing) route group is stripped ENTIRELY from the URL.
    assert "/about" in route_qns
    assert "/(marketing)/about" not in route_qns

    # a [slug] param segment is kept literally.
    assert "/blog/[slug]" in route_qns

    # the matcher is NOT stripped -> two DISTINCT sibling routes that must never
    # collide under MERGE.
    assert "/items/[id=integer]" in route_qns
    assert "/items/[id=hex]" in route_qns
    assert "/items/[id=integer]" != "/items/[id=hex]"


def test_ac3_idempotent():
    def ids():
        ir = dispatch(FIXTURE, PROV, repo_name="app")
        nodes = frozenset((n.kind, n.qualified_name) for n in ir.nodes)
        edges = frozenset((e.kind, e.src, e.dst) for e in ir.edges)
        return nodes, edges

    # extracting the SAME tree twice yields identical node/edge id sets.
    assert ids() == ids()


# --- ac-4: GUARDS inheritance (layout vs hook) ---


def test_ac4_layout_guards_descendant_route_never_endpoint():
    # this fixture has NO hooks.server -> the ONLY guard source is the server Layout.
    ir = _ir()

    # a page under a +layout.server.ts is guarded by that server Layout.
    assert ir.has_edge(GUARDS, "layout:/admin", "/admin/dashboard")

    # keystone: a +server Endpoint under the same guarded layout is NOT guarded
    # (a +server bypasses layout load; a Layout never guards an Endpoint).
    guarded_dsts = {e.dst for e in ir.edges_of(GUARDS)}
    assert "DELETE /admin/dangerous" not in guarded_dsts


def test_ac4_server_hook_guards_every_endpoint_and_route(tmp_path):
    ir = _ir_hooked(tmp_path)
    hook_qn = "hook:src/hooks.server.ts"
    # a server Hook guards EVERY Endpoint AND EVERY page Route.
    assert ir.has_edge(GUARDS, hook_qn, "GET /secure")
    assert ir.has_edge(GUARDS, hook_qn, "/")


# --- ac-6: server_only marker (post-mutation over already-collected FILE nodes) ---


def test_ac6_server_only_marker():
    ir = _ir()

    def server_only(path):
        n = ir.node(path)
        assert n is not None and n.kind == FILE, path
        return n.server_only

    # .server infix / +server / under src/lib/server -> True.
    assert server_only("src/routes/blog/[slug]/+page.server.ts") is True
    assert server_only("src/routes/api/users/[id]/+server.ts") is True
    assert server_only("src/routes/admin/+layout.server.ts") is True
    assert server_only("src/lib/server/secrets.ts") is True

    # a universal component / util is NOT server-only (stays None).
    assert server_only("src/routes/+page.svelte") is None
    assert server_only("src/lib/util.ts") is None
