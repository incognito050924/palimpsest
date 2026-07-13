"""TDD for the Rust static extractor (다언어 온톨로지 — issue #12).

Hermetic: extracts an IR from an inline ``{relpath: rust-source}`` corpus written
under ``tmp_path`` — never an external path. Mirrors the Kotlin/Python extractors'
contract (ADR-20260706 §결정6: per-language queries/<lang>/*.scm, the resolver stays
language-neutral).

The load-bearing Rust facts these tests pin:
  - Rust has NO class; a free ``fn`` and an ``impl``/``trait`` method share ONE
    grammar node (``function_item``). Parent context is the ONLY discriminator — a
    ``function_item`` directly under a module is a Function, one under an
    ``impl_item``/``trait_item`` body is a Method (ac-1).
  - ``struct``/``enum``/``trait`` are the CLASS surface; an ``impl`` block's methods
    attach to the implemented type (ac-2).
  - ``qualified_name`` reflects the inline ``mod`` path (module-path scheme, A-Q6),
    with simple type-name parameter identity (Rust is statically typed) (ac-5).
"""

from pathlib import Path

from palimpsest.ir import Provenance, FILE, CLASS, METHOD, FUNCTION
from palimpsest.extract.rust import extract

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

RUST_SRC = """use std::collections::HashMap;
use crate::util::helper;

fn main() {
    greet("hi");
    let s = Service::new();
    s.handle();
}

fn greet(name: &str) {
}

struct Service {
    count: u32,
}

enum Color {
    Red,
    Green,
}

impl Service {
    fn new() -> Service {
        Service { count: 0 }
    }
    fn handle(&self) {
        greet("x");
    }
}

trait Greeter {
    fn hello(&self);
}

mod inner {
    fn helper_fn() {
        top_level_helper();
    }
}

fn top_level_helper() {
}
"""


def _extract(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="R")


def test_free_fn_is_function_contained_by_file(tmp_path):
    # ac-1: a `fn` directly under the module is a Function (NOT a Method), grounded
    ir = _extract(tmp_path, {"app.rs": RUST_SRC})
    fn = ir.node("main()")
    assert fn is not None and fn.kind == FUNCTION
    assert fn.name == "main"
    assert fn.path == "app.rs"
    assert fn.start_line == 4  # `fn main() {`
    # ac-5: simple type-name parameter identity (`&str` -> `str`)
    greet = ir.node("greet(str)")
    assert greet is not None and greet.kind == FUNCTION
    # (:File)-[:CONTAINS]->(:Function)
    assert ir.node("app.rs") is not None and ir.node("app.rs").kind == FILE
    assert ir.has_edge("CONTAINS", "app.rs", "main()")
    assert ir.has_edge("CONTAINS", "app.rs", "greet(str)")


def test_struct_and_enum_and_trait_are_classes(tmp_path):
    # ac-2: struct/enum/trait are the CLASS surface, each contained by the File
    ir = _extract(tmp_path, {"app.rs": RUST_SRC})
    for name in ("Service", "Color", "Greeter"):
        cls = ir.node(name)
        assert cls is not None and cls.kind == CLASS, name
        assert ir.has_edge("CONTAINS", "app.rs", name)


def test_impl_method_is_method_contained_by_type(tmp_path):
    # ac-2: an impl block's `fn` is a Method attached to the implemented type
    ir = _extract(tmp_path, {"app.rs": RUST_SRC})
    m = ir.node("Service#new()")
    assert m is not None and m.kind == METHOD
    assert m.name == "new"
    handle = ir.node("Service#handle()")  # `&self` receiver dropped from identity
    assert handle is not None and handle.kind == METHOD
    assert ir.has_edge("CONTAINS", "Service", "Service#new()")
    assert ir.has_edge("CONTAINS", "Service", "Service#handle()")
    # the method is NOT emitted as a top-level Function
    assert ir.node("new()") is None
    assert ir.node("handle()") is None


def test_trait_signature_method(tmp_path):
    # ac-2: a signature-only trait method is still a Method of the trait
    ir = _extract(tmp_path, {"app.rs": RUST_SRC})
    hello = ir.node("Greeter#hello()")
    assert hello is not None and hello.kind == METHOD
    assert ir.has_edge("CONTAINS", "Greeter", "Greeter#hello()")


def test_inline_mod_path_prefix(tmp_path):
    # ac-5: qualified_name reflects the inline `mod` path (module-path scheme, A-Q6)
    ir = _extract(tmp_path, {"app.rs": RUST_SRC})
    fn = ir.node("inner::helper_fn()")
    assert fn is not None and fn.kind == FUNCTION
    assert fn.name == "helper_fn"
    # the container is still the File (module de-Class'd), never a mod node
    assert ir.has_edge("CONTAINS", "app.rs", "inner::helper_fn()")


def test_calls_edges_name_based(tmp_path):
    # ac-3: name-based CALLS, both Function and Method as source, no self-loops
    ir = _extract(tmp_path, {"app.rs": RUST_SRC})
    assert ir.has_edge("CALLS", "main()", "greet(str)")           # bare call `greet(..)`
    assert ir.has_edge("CALLS", "main()", "Service#new()")        # scoped `Service::new()`
    assert ir.has_edge("CALLS", "main()", "Service#handle()")     # method `s.handle()`
    assert ir.has_edge("CALLS", "Service#handle()", "greet(str)")  # a Method as source
    assert ir.has_edge("CALLS", "inner::helper_fn()", "top_level_helper()")
    assert not any(e.src == e.dst for e in ir.edges_of("CALLS"))


def test_imports_and_honest_external(tmp_path):
    # ac-4: each `use` yields a File->module IMPORTS edge; external targets get NO node
    ir = _extract(tmp_path, {"app.rs": RUST_SRC})
    assert ir.has_edge("IMPORTS", "app.rs", "std::collections::HashMap")
    assert ir.has_edge("IMPORTS", "app.rs", "crate::util::helper")
    # honest external — the unresolved module target is a dangling edge, no phantom node
    assert ir.node("std::collections::HashMap") is None


def test_provenance_on_all(tmp_path):
    ir = _extract(tmp_path, {"app.rs": RUST_SRC})
    assert ir.nodes and ir.edges
    for n in ir.nodes:
        assert n.provenance == PROV
    for e in ir.edges:
        assert e.provenance == PROV


def test_rust_query_is_per_language_and_valid():
    # ADR-20260706 §결정6: the tags query lives as a separate per-language file the
    # extractor loads at runtime and that compiles against the Rust grammar.
    from tree_sitter import Language, Query
    import tree_sitter_rust as tsrust
    from palimpsest.extract import rust as rmod

    qdir = Path(rmod.__file__).parent / "queries" / "rust"
    tags = qdir / "tags.scm"
    assert tags.is_file() and tags.read_text().strip()
    Query(Language(tsrust.language()), tags.read_text())  # must compile
    assert rmod._QUERY_DIR == qdir
