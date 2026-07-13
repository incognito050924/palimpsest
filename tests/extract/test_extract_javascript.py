"""TDD for the JavaScript (.js/.jsx) static extractor (wi_260713lom, n4).

Hermetic inline corpus under ``tmp_path``. Same ECMAScript core as the TypeScript
extractor, over the javascript grammar. JS carries no type annotations, so every
identity paramType degrades to ``?`` — but the structural ontology (Function /
Class / Method / CONTAINS / CALLS / IMPORTS) is identical (ac-1).
"""

from pathlib import Path

from palimpsest.ir import Provenance, REPO, PACKAGE, FILE, CLASS, METHOD, FUNCTION
from palimpsest.extract.javascript import extract

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

APP_JS = """import { helper } from './util';

export function greet(name) {
    helper();
}

export const render = (props) => {
    greet();
};

class Service {
    handle() {
        greet();
    }
}
"""

UTIL_JS = """export function helper() {
}
"""


def _extract(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="J")


def test_functions_arrow_class_method(tmp_path):
    ir = _extract(tmp_path, {"app.js": APP_JS, "util.js": UTIL_JS})
    # JS param types degrade to `?` (no annotations)
    greet = ir.node("app.js.greet(?)")
    assert greet is not None and greet.kind == FUNCTION
    assert greet.name == "greet"
    render = ir.node("app.js.render(?)")
    assert render is not None and render.kind == FUNCTION
    cls = ir.node("app.js.Service")
    assert cls is not None and cls.kind == CLASS
    m = ir.node("app.js.Service#handle()")
    assert m is not None and m.kind == METHOD
    assert ir.node("app.js") is not None and ir.node("app.js").kind == FILE
    assert ir.has_edge("CONTAINS", "app.js", "app.js.greet(?)")
    assert ir.has_edge("CONTAINS", "app.js", "app.js.render(?)")
    assert ir.has_edge("CONTAINS", "app.js", "app.js.Service")
    assert ir.has_edge("CONTAINS", "app.js.Service", "app.js.Service#handle()")


def test_calls_and_imports(tmp_path):
    ir = _extract(tmp_path, {"app.js": APP_JS, "util.js": UTIL_JS})
    assert ir.has_edge("CALLS", "app.js.greet(?)", "util.js.helper()")
    assert ir.has_edge("CALLS", "app.js.render(?)", "app.js.greet(?)")
    assert ir.has_edge("CALLS", "app.js.Service#handle()", "app.js.greet(?)")
    assert ir.has_edge("IMPORTS", "app.js", "util.js")


def test_repo_containment_no_packages(tmp_path):
    ir = _extract(tmp_path, {"app.js": APP_JS, "util.js": UTIL_JS})
    assert len(ir.nodes_of(REPO)) == 1
    assert ir.nodes_of(PACKAGE) == []
    assert ir.has_edge("CONTAINS", "J", "app.js")
    for e in ir.edges:
        assert e.provenance == PROV


def test_javascript_query_compiles():
    from tree_sitter import Language, Query
    import tree_sitter_javascript as tsjavascript
    from palimpsest.extract import ecmascript as emod

    tags = Path(emod.__file__).parent / "queries" / "ecmascript" / "tags.scm"
    assert tags.is_file() and tags.read_text().strip()
    Query(Language(tsjavascript.language()), tags.read_text())  # must compile
