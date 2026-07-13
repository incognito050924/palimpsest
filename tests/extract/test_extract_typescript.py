"""TDD for the TypeScript (.ts/.tsx) static extractor (wi_260713lom, n4).

Hermetic: extracts an IR from an inline ``{relpath: ts-source}`` corpus written
under ``tmp_path`` — never an external path. Mirrors the Kotlin extractor's
contract (ADR-20260706 §결정6: per-language queries/<lang>/*.scm, language-neutral
resolver), extended for the ECMAScript family.

Load-bearing facts these tests pin (ac-1, ac-6):
  - A top-level ``function`` AND a top-level arrow / function-expression ``const``
    (``const C = () => {}`` / ``const f = function(){}``) are BOTH (:File)-[:CONTAINS]
    ->(:Function). The arrow/expression form is the dominant React/TS shape — without
    it a React repo indexes almost nothing.
  - A ``class`` method is (:Class)-[:CONTAINS]->(:Method); the class is
    (:File)-[:CONTAINS]->(:Class).
  - qualified_name is the module-path scheme (ac-6): Function ``{modpath}.{name}
    ({paramTypes})``, Class ``{modpath}.{Class}``, Method ``{classFqn}#{name}
    ({paramTypes})`` — modpath = repo-relative posix path (= File qualified_name).
  - CALLS is name-based and resolves SAME-FAMILY cross-file (a .ts calling a .ts util).
  - IMPORTS: a relative specifier resolves to the target File's qualified_name.
  - .tsx routes through the tsx grammar (JSX parses) while .ts routes through the
    typescript grammar.
"""

from pathlib import Path

from palimpsest.ir import Provenance, REPO, PACKAGE, FILE, CLASS, METHOD, FUNCTION
from palimpsest.extract.typescript import extract

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

APP_TS = """import { helper } from './util';

export function greet(name: string): void {
    helper();
}

export const render = (props: Props) => {
    greet('x');
};

const legacy = function(n: number) {
    return n;
};

export class Service {
    handle(req: Req): void {
        greet('a');
    }
}
"""

UTIL_TS = """export function helper() {
}
"""

WIDGET_TSX = """export const Widget = () => {
    return <div onClick={greet} />;
};
"""


def _extract(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="T")


def test_top_level_function_and_arrow_const_are_functions(tmp_path):
    ir = _extract(tmp_path, {"app.ts": APP_TS, "util.ts": UTIL_TS})
    # plain `function` -> Function, with param type in the identity (ac-6)
    greet = ir.node("app.ts.greet(string)")
    assert greet is not None and greet.kind == FUNCTION
    assert greet.name == "greet"
    assert greet.path == "app.ts"
    assert greet.start_line == 3  # `export function greet(...)`
    # arrow-const `const render = (props) => {}` -> Function (dominant React form)
    render = ir.node("app.ts.render(Props)")
    assert render is not None and render.kind == FUNCTION
    assert render.name == "render"
    # function-expression-const `const legacy = function(){}` -> Function
    legacy = ir.node("app.ts.legacy(number)")
    assert legacy is not None and legacy.kind == FUNCTION
    # (:File)-[:CONTAINS]->(:Function)
    assert ir.node("app.ts") is not None and ir.node("app.ts").kind == FILE
    assert ir.has_edge("CONTAINS", "app.ts", "app.ts.greet(string)")
    assert ir.has_edge("CONTAINS", "app.ts", "app.ts.render(Props)")
    assert ir.has_edge("CONTAINS", "app.ts", "app.ts.legacy(number)")


def test_class_method_is_method_contained_by_class(tmp_path):
    ir = _extract(tmp_path, {"app.ts": APP_TS, "util.ts": UTIL_TS})
    cls = ir.node("app.ts.Service")
    assert cls is not None and cls.kind == CLASS
    m = ir.node("app.ts.Service#handle(Req)")
    assert m is not None and m.kind == METHOD
    assert m.name == "handle"
    assert ir.has_edge("CONTAINS", "app.ts", "app.ts.Service")
    assert ir.has_edge("CONTAINS", "app.ts.Service", "app.ts.Service#handle(Req)")
    # the method is NOT emitted as a top-level Function
    assert ir.node("app.ts.handle(Req)") is None


def test_calls_edges_name_based_same_family_cross_file(tmp_path):
    ir = _extract(tmp_path, {"app.ts": APP_TS, "util.ts": UTIL_TS})
    # a .ts function calling a .ts util resolves SAME-FAMILY cross-file (KEY DECISION 1)
    assert ir.has_edge("CALLS", "app.ts.greet(string)", "util.ts.helper()")
    # render() calls greet(); handle() calls greet()
    assert ir.has_edge("CALLS", "app.ts.render(Props)", "app.ts.greet(string)")
    assert ir.has_edge("CALLS", "app.ts.Service#handle(Req)", "app.ts.greet(string)")
    # no self-loops
    assert not any(e.src == e.dst for e in ir.edges_of("CALLS"))


def test_imports_relative_specifier_resolves_to_file(tmp_path):
    ir = _extract(tmp_path, {"app.ts": APP_TS, "util.ts": UTIL_TS})
    # `import { helper } from './util'` -> resolved to the util.ts File qualified_name
    assert ir.has_edge("IMPORTS", "app.ts", "util.ts")


def test_tsx_routes_through_tsx_grammar(tmp_path):
    ir = _extract(tmp_path, {"widget.tsx": WIDGET_TSX})
    # JSX only parses under the tsx grammar; the arrow-const component is a Function
    w = ir.node("widget.tsx.Widget()")
    assert w is not None and w.kind == FUNCTION
    assert w.path == "widget.tsx"


def test_repo_containment_no_packages(tmp_path):
    ir = _extract(tmp_path, {"app.ts": APP_TS, "util.ts": UTIL_TS})
    repos = ir.nodes_of(REPO)
    assert len(repos) == 1 and repos[0].qualified_name == "T"
    # ECMAScript has no packages: ONE Repo + Repo-CONTAINS->File, NO Package nodes
    assert ir.nodes_of(PACKAGE) == []
    assert ir.has_edge("CONTAINS", "T", "app.ts")
    assert ir.has_edge("CONTAINS", "T", "util.ts")
    # provenance stamped everywhere
    for n in ir.nodes:
        assert n.provenance == PROV
    for e in ir.edges:
        assert e.provenance == PROV


def test_typescript_query_compiles_against_ts_and_tsx():
    # ADR-20260706 §결정6: the shared tags query lives as a per-language file the
    # extractor loads at runtime and that compiles against BOTH TS grammars.
    from tree_sitter import Language, Query
    import tree_sitter_typescript as tstypescript
    from palimpsest.extract import ecmascript as emod

    qdir = Path(emod.__file__).parent / "queries" / "ecmascript"
    tags = qdir / "tags.scm"
    assert tags.is_file() and tags.read_text().strip()
    Query(Language(tstypescript.language_typescript()), tags.read_text())  # must compile
    Query(Language(tstypescript.language_tsx()), tags.read_text())  # must compile


# ── is_test marker (issue #17: multilang test-impact) ──────────────────────────
# Signals via the shared ecmascript core: *.test.* / *.spec.* filename, or a
# jest/vitest/mocha import. Marks the file's code-unit nodes; production stays falsy.

_TS_TEST = 'export function checkRender() {\n  render();\n}\n'
_TS_PROD = 'export function render(): string {\n  return "w";\n}\n'
_TS_IMPORT_ONLY = 'import { expect } from "vitest";\n\nexport function helper() {}\n'


def test_is_test_marked_by_test_filename(tmp_path):
    ir = _extract(tmp_path, {"widget.test.ts": _TS_TEST, "widget.ts": _TS_PROD})
    for n in ir.nodes:
        if n.path == "widget.test.ts" and n.kind in (FILE, CLASS, METHOD, FUNCTION):
            assert n.is_test is True, (n.kind, n.qualified_name)
        if n.path == "widget.ts":
            assert not n.is_test, (n.kind, n.qualified_name)


def test_is_test_marked_by_spec_filename(tmp_path):
    ir = _extract(tmp_path, {"widget.spec.tsx": _TS_TEST})
    fns = [n for n in ir.nodes if n.kind == FUNCTION]
    assert fns and all(n.is_test for n in fns)


def test_is_test_marked_by_vitest_import(tmp_path):
    # NOT test-named, but imports vitest -> a test file by the import signal.
    ir = _extract(tmp_path, {"svc.ts": _TS_IMPORT_ONLY})
    code = [n for n in ir.nodes if n.kind in (FILE, FUNCTION)]
    assert code and all(n.is_test for n in code)


def test_is_test_never_marks_repo(tmp_path):
    ir = _extract(tmp_path, {"widget.test.ts": _TS_TEST})
    assert any(n.kind == FUNCTION and n.is_test for n in ir.nodes)
    for n in ir.nodes:
        if n.kind == REPO:
            assert not n.is_test
