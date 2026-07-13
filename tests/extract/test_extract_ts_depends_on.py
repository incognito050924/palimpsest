"""TDD for TypeScript type-based DEPENDS_ON (wi_260713lom, n6 — ac-4).

WHY these tests exist (ac-4 asymmetry):
  DEPENDS_ON (Class->Class) is a TS-ONLY edge. TS carries field/parameter type
  annotations; JS does not. So a `.ts` `dep: Foo` where `class Foo` exists in the
  SAME language family must emit a DEPENDS_ON, while a `.js` corpus emits NONE —
  the asymmetry is verified by ABSENCE (an empty edge set is falsifiable-by-silence,
  so we pair it with a positive control in the same suite).

  The asymmetry is enforced two ways that these tests pin:
    1. behavioral — the TS `LangProfile` sets ``collect_types=True``; JS keeps it
       False, so the JS walker collects no type refs and emits zero DEPENDS_ON.
    2. structural — ``queries/typescript/types.scm`` references TS-only grammar node
       types (``public_field_definition``/``required_parameter``/``type_annotation``)
       so it compiles ONLY against the TS grammar and RAISES against the JS grammar
       (the query-compile boundary — a JS build can never even load the type query).

  SC-B (cross-language leak): DEPENDS_ON is name-based (resolve simple type name ->
  Class by unqualified name), so it could false-match across languages exactly like
  CALLS. The resolver MUST run per fragment over that fragment's own nodes: a TS
  ``x: Foo`` must bind to a ``class Foo`` in ITS family, never to a same-named
  ``class Foo`` in a ``.js`` fragment (which would falsely merge communities).

Container keying (the de-Class generalization):
  - class field / method parameter type -> keyed by the enclosing CLASS fqn
    (Class -> Class).
  - top-level function parameter type -> keyed by the FILE (Module) fqn
    (Module/File -> Class) — a top-level function has no declaring class.
"""

from palimpsest.ir import Provenance, CLASS, DEPENDS_ON, CALLS, IMPORTS, FILE
from palimpsest.extract.typescript import extract as extract_ts
from palimpsest.extract.javascript import extract as extract_js
from palimpsest.extract import extract_ecmascript

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)


def _write(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)


def _extract_ts(tmp_path, files):
    _write(tmp_path, files)
    return extract_ts(tmp_path, PROV, repo_name="T")


def _extract_js(tmp_path, files):
    _write(tmp_path, files)
    return extract_js(tmp_path, PROV, repo_name="J")


def _extract_mixed(tmp_path, files):
    _write(tmp_path, files)
    return extract_ecmascript(tmp_path, PROV, repo_name="U")


# --- ac-4 positive: TS class field type -> DEPENDS_ON (Class->Class) ---

FIELD_TS = """export class Service {
    dep: Foo;
    other: Bar = null;
}

export class Foo {}

export class Bar {}
"""


def test_ts_class_field_type_creates_depends_on(tmp_path):
    ir = _extract_ts(tmp_path, {"app.ts": FIELD_TS})
    # a field annotation `dep: Foo` makes Service DEPENDS_ON Foo (Class -> Class),
    # resolved by matching the simple type name against a CLASS node.
    assert ir.has_edge(DEPENDS_ON, "app.ts.Service", "app.ts.Foo")
    assert ir.has_edge(DEPENDS_ON, "app.ts.Service", "app.ts.Bar")
    # no self-loops, no wrong direction (Foo/Bar depend on nothing here)
    assert not any(e.src == e.dst for e in ir.edges_of(DEPENDS_ON))
    assert not any(e.src == "app.ts.Foo" for e in ir.edges_of(DEPENDS_ON))


# --- ac-4 positive: TS method parameter type -> DEPENDS_ON (Class->Class) ---

PARAM_TS = """export class Handler {
    run(req: Request, aux: Helper): void {}
}

export class Request {}

export class Helper {}
"""


def test_ts_method_param_type_creates_depends_on(tmp_path):
    ir = _extract_ts(tmp_path, {"h.ts": PARAM_TS})
    # a method parameter type is a dependency of the DECLARING class (not the method)
    assert ir.has_edge(DEPENDS_ON, "h.ts.Handler", "h.ts.Request")
    assert ir.has_edge(DEPENDS_ON, "h.ts.Handler", "h.ts.Helper")


# --- ac-4 positive: top-level function param -> Module(File)->Class (de-Class) ---

TOPFN_TS = """export function build(cfg: Config): void {}

export class Config {}
"""


def test_ts_toplevel_function_param_creates_module_depends_on(tmp_path):
    ir = _extract_ts(tmp_path, {"mod.ts": TOPFN_TS})
    # a top-level function has no declaring class, so its parameter-type dependency
    # is keyed by the FILE (Module) fqn — the de-Class generalization.
    assert ir.node("mod.ts") is not None and ir.node("mod.ts").kind == FILE
    assert ir.has_edge(DEPENDS_ON, "mod.ts", "mod.ts.Config")


# --- ac-4 asymmetry: a JS corpus emits ZERO DEPENDS_ON (verified by absence) ---

JS_WITH_CLASSES = """export class Service {
    handle() {
        return new Foo();
    }
}

export class Foo {}
"""


def test_js_corpus_emits_no_depends_on(tmp_path):
    # JS carries no type annotations; the JS profile has collect_types=False, so no
    # DEPENDS_ON is ever emitted even when same-named classes coexist (asymmetry).
    ir = _extract_js(tmp_path, {"app.js": JS_WITH_CLASSES})
    assert ir.edges_of(DEPENDS_ON) == []


# --- ac-4 SC-B: a TS `x: Foo` must NOT bind to a `class Foo` in a .js fragment ---

CONSUMER_TS = """import './legacy';

export class Consumer {
    dep: Foo;
}

export class Foo {}
"""

LEGACY_JS = """export class Foo {}
"""


def test_ts_depends_on_does_not_cross_language_boundary(tmp_path):
    ir = _extract_mixed(tmp_path, {"consumer.ts": CONSUMER_TS, "legacy.js": LEGACY_JS})
    # positive control: the TS ref binds to the SAME-family class Foo
    assert ir.has_edge(DEPENDS_ON, "consumer.ts.Consumer", "consumer.ts.Foo")
    # SC-B: it must NOT bind to the same-named `class Foo` in the .js fragment
    assert not ir.has_edge(DEPENDS_ON, "consumer.ts.Consumer", "legacy.js.Foo")
    assert not any(e.dst == "legacy.js.Foo" for e in ir.edges_of(DEPENDS_ON))
    # the cross-language link stays IMPORTS-only, and CALLS never crosses either
    assert ir.has_edge(IMPORTS, "consumer.ts", "legacy.js")
    assert not any(
        e.src.endswith(".js") or e.dst.endswith(".js") for e in ir.edges_of(CALLS)
    )


# --- ac-4 structural boundary: types.scm compiles vs TS, RAISES vs JS ---

def test_types_query_compiles_ts_only():
    from pathlib import Path
    from tree_sitter import Language, Query
    import tree_sitter_typescript as tstypescript
    import tree_sitter_javascript as tsjavascript
    from palimpsest.extract import ecmascript as emod

    qpath = Path(emod.__file__).parent / "queries" / "typescript" / "types.scm"
    assert qpath.is_file() and qpath.read_text().strip()
    text = qpath.read_text()
    # compiles against BOTH TS grammars
    Query(Language(tstypescript.language_typescript()), text)
    Query(Language(tstypescript.language_tsx()), text)
    # RAISES against the JS grammar — the asymmetry is structural, not just a flag.
    import pytest

    with pytest.raises(Exception):
        Query(Language(tsjavascript.language()), text)
