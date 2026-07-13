"""TDD for the unified ECMAScript driver (wi_260713lom, n4 — ac-2, SC-B).

The cross-language boundary (KEY DECISION 1+2 from the n2 design):

  * CALLS is name-based and would false-match across languages, and (via
    community.py) that false CALLS would falsely merge communities. So each
    fragment (ts = .ts+.tsx, js = .js+.jsx) resolves CALLS over ITS OWN nodes
    only; the driver NEVER re-runs a union-wide CALLS pass. A .ts function and a
    same-named .js function are NOT linked by CALLS (SC-B negative).
  * The ONLY cross-language connection is IMPORTS: each walker emits a raw
    ``IMPORTS(file, specifier)``; the driver's union-wide ``_resolve_imports``
    rewrites a relative specifier to the target File's qualified_name — so a
    ``.ts`` importing a ``.js`` connects into one component WITHOUT a false CALLS.

  * DEPENDS_ON is NOT emitted by this node (n6's job): the whole IR has zero
    DEPENDS_ON edges here.
"""

from palimpsest.ir import Provenance, REPO, PACKAGE, CALLS, DEPENDS_ON, IMPORTS, FILE
from palimpsest.extract import extract_ecmascript

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

# a.ts imports ./b (which resolves to b.js) and defines a `shared` fn PLUS a caller
# that calls shared(). b.js defines a same-named `shared`. The .ts caller must link
# ONLY to the .ts shared, never across the family boundary to b.js.shared.
A_TS = """import './b';

export function shared() {
}

export function caller() {
    shared();
}
"""

B_JS = """export function shared() {
}
"""


def _extract(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract_ecmascript(tmp_path, PROV, repo_name="U")


def test_relative_import_resolves_across_language_boundary(tmp_path):
    ir = _extract(tmp_path, {"a.ts": A_TS, "b.js": B_JS})
    # a.ts import './b' -> b.js (ext-probe found .js). This is the cross-language link.
    assert ir.has_edge(IMPORTS, "a.ts", "b.js")


def test_single_connected_component_across_ts_and_js(tmp_path):
    ir = _extract(tmp_path, {"a.ts": A_TS, "b.js": B_JS})
    # both files present, joined by the resolved IMPORTS edge -> one component
    files = {n.qualified_name for n in ir.nodes_of(FILE)}
    assert files == {"a.ts", "b.js"}
    # undirected reachability over IMPORTS: a.ts <-> b.js
    imports = {(e.src, e.dst) for e in ir.edges_of(IMPORTS)}
    connected = ("a.ts", "b.js") in imports or ("b.js", "a.ts") in imports
    assert connected


def test_no_calls_span_the_language_boundary(tmp_path):
    ir = _extract(tmp_path, {"a.ts": A_TS, "b.js": B_JS})
    # the .ts caller() calls shared() -> links ONLY to the .ts shared, NOT b.js.shared
    assert ir.has_edge(CALLS, "a.ts.caller()", "a.ts.shared()")
    # SC-B: no CALLS edge touches the .js `shared` (would be a false cross-language merge)
    assert not any(e.dst == "b.js.shared()" for e in ir.edges_of(CALLS))
    assert not any(e.src == "b.js.shared()" for e in ir.edges_of(CALLS))


def test_no_depends_on_emitted(tmp_path):
    ir = _extract(tmp_path, {"a.ts": A_TS, "b.js": B_JS})
    # DEPENDS_ON is node n6's job — this node emits none.
    assert ir.edges_of(DEPENDS_ON) == []


def test_repo_node_no_packages(tmp_path):
    ir = _extract(tmp_path, {"a.ts": A_TS, "b.js": B_JS})
    repos = ir.nodes_of(REPO)
    assert len(repos) == 1 and repos[0].qualified_name == "U"
    assert ir.nodes_of(PACKAGE) == []
    assert ir.has_edge("CONTAINS", "U", "a.ts")
    assert ir.has_edge("CONTAINS", "U", "b.js")
