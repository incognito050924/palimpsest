"""TDD for the SvelteKit routing recall channels (issue #9 — recall/routing.py).

WHY these tests exist:
  ``recall/routing.py`` adds read-only, grounded recall over the Route/Endpoint/Layout/
  Hook ontology the SvelteKit extractor materializes. Each channel answers one routing
  question and returns the SAME ``{items, sources, ..., gaps}`` shape as the rest of
  recall. These tests pin the four ACs on a REAL ingested fixture (two small SvelteKit
  trees extracted by the production ``dispatch`` and ingested by the real ``ingest``), so
  the queries are exercised over real Cypher / real ontology, not a hand-built graph.

  Two fixture apps, deliberately SEPARATE (mirrors tests/extract/test_extract_sveltekit.py):
  ``hooks.server`` flips GLOBAL guard behavior — it guards EVERY endpoint — so folding it
  into the layout-only app would erase the keystone unguarded-endpoint case (ac-4). They
  are ingested ADDITIVELY onto the shared graph under URL namespaces (``/admin/*`` vs
  ``/vault`` / ``/``) that never collide (same additive pattern as ``test_impact_db``).

What each AC pins:
  * ac-4: ``recall_unguarded_endpoints`` flags the layout-only ``+server`` endpoint (a
    server Layout NEVER guards an Endpoint — the keystone) and does NOT flag the endpoint
    guarded by ``hooks.server``; the result ALWAYS carries the static-lower-bound
    soundness disclosure (a missing GUARDS edge is not a definitive "unprotected" verdict).
  * ac-5: ``recall_endpoint_reachable`` returns the sensitive function reachable from the
    endpoint's handler via HANDLES -> CALLS, and the traversal is bounded (a named
    per-frontier fan-out cap, mirroring recall_test_impact / recall_cochange — never a
    var-length CALLS path).
  * ac-6: ``recall_server_only_imports`` returns the universal-file -> server-only-module
    import (a client-bundle leak).
  * URL<->file: ``recall_route_files`` / ``recall_file_route`` round-trip a page Route to
    its ``+page`` File and back, over the REALIZES spine.

Live-Neo4j (Docker): requires a Neo4j testcontainer; where Docker is unavailable these
ERROR on the fixture (a Docker-fixture error, distinct from a collection/import failure).
"""

import pytest

from palimpsest.extract import dispatch
from palimpsest.ir import Provenance
from palimpsest.kg import ingest
from palimpsest.recall.routing import (
    recall_unguarded_endpoints,
    recall_endpoint_reachable,
    recall_route_files,
    recall_file_route,
    recall_server_only_imports,
    _UNGUARDED_STATIC_LOWER_BOUND_GAP,
    _REACHABLE_FANOUT_CAP,
)

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

# ── App A: a server Layout guards a page Route, but NEVER the +server endpoint under it
# (the keystone). NO hooks.server here, so the endpoint has NO incoming GUARDS. The
# endpoint's handler CALLS a sensitive server-only function (ac-5), and a UNIVERSAL util
# imports that server-only module via a relative specifier (ac-6 leak). Repo "routing".
APP_A = {
    "src/routes/admin/+layout.server.ts": (
        "export const load = async () => ({ user: null });\n"
    ),
    "src/routes/admin/dashboard/+page.svelte": "<h1>dashboard</h1>\n",
    "src/routes/admin/danger/+server.ts": (
        "import { dropAllTables } from '../../../lib/server/danger';\n"
        "export function DELETE() {\n"
        "  dropAllTables();\n"
        "  return new Response('ok');\n"
        "}\n"
    ),
    "src/lib/server/danger.ts": (
        "export function dropAllTables() {\n  return true;\n}\n"
    ),
    # universal util (NOT server-only) importing the server-only module -> ac-6 leak.
    "src/lib/leaky.ts": (
        "import { dropAllTables } from './server/danger';\n"
        "export const NAME = 'leaky';\n"
    ),
}

# ── App B: a server Hook (hooks.server.ts) guards EVERY endpoint AND page route, so its
# +server endpoint IS guarded — the negative control for ac-4. Repo "routing_hooked".
APP_B = {
    "src/hooks.server.ts": (
        "export const handle = async ({ event, resolve }) => resolve(event);\n"
    ),
    "src/routes/+page.svelte": "<h1>home</h1>\n",
    "src/routes/vault/+server.ts": (
        "export function GET() {\n  return new Response('vault');\n}\n"
    ),
}

# ids verified by running the real extractor (dispatch) over the trees above.
UNGUARDED_EP = "DELETE /admin/danger"       # layout-only endpoint — NO incoming GUARDS
GUARDED_EP = "GET /vault"                    # guarded by hooks.server
HANDLER = "src/routes/admin/danger/+server.ts.DELETE()"
SENSITIVE_FN = "src/lib/server/danger.ts.dropAllTables()"
LEAKY_FILE = "src/lib/leaky.ts"              # universal (server_only IS NULL)
SERVER_ONLY_MODULE = "src/lib/server/danger.ts"  # server_only=true
PAGE_URL = "/admin/dashboard"
PAGE_FILE = "src/routes/admin/dashboard/+page.svelte"


@pytest.fixture(scope="module")
def routing_db(recall_db, tmp_path_factory):
    """Two SvelteKit apps ingested ADDITIVELY onto the shared graph (mirrors
    ``test_impact_db``): real extract (dispatch) + real ingest, so server_only / GUARDS /
    HANDLES / REALIZES are stamped by the production path. URL namespaces never collide
    with the commute (kr.co.*) / test-impact (app.*) corpora."""
    for repo, files in (("routing", APP_A), ("routing_hooked", APP_B)):
        root = tmp_path_factory.mktemp(repo)
        for rel, src in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(src)
        ingest(recall_db, dispatch(root, PROV, repo_name=repo))
    return recall_db


# ── ac-4: unguarded endpoints + soundness disclosure ─────────────────────────


def test_ac4_flags_layout_only_endpoint_not_hook_guarded(routing_db):
    """The layout-only ``+server`` endpoint is flagged (a server Layout never guards an
    Endpoint — the keystone bypass), while the endpoint under a ``hooks.server`` IS
    guarded and must NOT be flagged."""
    out = recall_unguarded_endpoints(routing_db)
    ids = {it["id"] for it in out["items"]}
    assert UNGUARDED_EP in ids       # layout-only, no hooks.server -> no GUARDS
    assert GUARDED_EP not in ids     # guarded by hooks.server


def test_ac4_result_carries_soundness_lower_bound_disclosure(routing_db):
    """ac-4 soundness: the result ALWAYS carries the static-lower-bound disclosure — a
    missing GUARDS edge is 'no statically-detected guard', never a definitive
    'unprotected' verdict (a runtime guard may still exist)."""
    out = recall_unguarded_endpoints(routing_db)
    assert _UNGUARDED_STATIC_LOWER_BOUND_GAP in out["gaps"]
    # the disclosure is present even though there ARE flagged results.
    assert out["items"]


# ── ac-5: endpoint-reachable code, bounded ───────────────────────────────────


def test_ac5_sensitive_function_reachable_from_endpoint_handler(routing_db):
    """From the Endpoint, incoming HANDLES reaches the handler Function, then forward
    CALLS reaches the sensitive server-only function — the endpoint's transitive blast
    radius."""
    out = recall_endpoint_reachable(routing_db, UNGUARDED_EP)
    by_id = {it["id"]: it for it in out["items"]}
    assert HANDLER in by_id                       # handler via HANDLES (depth 1)
    assert by_id[HANDLER]["relation"] == "HANDLES"
    assert SENSITIVE_FN in by_id                  # sensitive fn via CALLS
    assert by_id[SENSITIVE_FN]["relation"] == "CALLS"


def test_ac5_traversal_is_bounded_by_named_fanout_cap():
    """ac-5 bounding: the per-frontier fan-out cap that stops a hot handler from
    exploding is a named positive constant (mirrors _TEST_IMPACT_FANOUT_CAP), never a
    magic literal — so the endpoint-directed BFS is COMPUTE-bounded, not a var-length
    CALLS path."""
    assert isinstance(_REACHABLE_FANOUT_CAP, int)
    assert _REACHABLE_FANOUT_CAP > 0


def test_ac5_unresolved_endpoint_is_a_gap(routing_db):
    """An endpoint id that does not resolve is stated as an explicit gap, never a
    confident empty answer; the static-lower-bound note is still present."""
    out = recall_endpoint_reachable(routing_db, "DELETE /nope/missing")
    assert out["items"] == []
    assert any("did not resolve" in g for g in out["gaps"])


# ── ac-6: universal code importing a server-only module ──────────────────────


def test_ac6_universal_import_of_server_only_module_is_flagged(routing_db):
    """The universal util importing the server-only module surfaces as a leak; the item
    is the importing (universal) File carrying the server-only target it imports."""
    out = recall_server_only_imports(routing_db)
    pairs = {(it["id"], it["server_only_module"]["id"]) for it in out["items"]}
    assert (LEAKY_FILE, SERVER_ONLY_MODULE) in pairs


# ── URL <-> file round-trip over REALIZES ────────────────────────────────────


def test_url_file_realizes_round_trip(routing_db):
    """A page Route round-trips to its ``+page`` File and back over the REALIZES spine."""
    files = recall_route_files(routing_db, PAGE_URL)
    assert PAGE_FILE in {it["id"] for it in files["items"]}

    routes = recall_file_route(routing_db, PAGE_FILE)
    assert PAGE_URL in {it["id"] for it in routes["items"]}
