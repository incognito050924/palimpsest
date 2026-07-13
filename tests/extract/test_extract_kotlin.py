"""TDD for the Kotlin static extractor (n3-impl-kotlin-extract).

Hermetic: extracts an IR from an inline ``{relpath: kotlin-source}`` corpus written
under ``tmp_path`` — never an external path. Mirrors the Java extractor's contract
(ADR-20260706 §결정6: per-language queries/<lang>/*.scm, language-neutral resolver).

The load-bearing Kotlin fact these tests pin: a top-level function and a class
method share ONE grammar node (function_declaration); parent context is the only
discriminator (source_file -> Function, class_body -> Method). ac-1 requires
top-level fun -> (:File)-[:CONTAINS]->(:Function), method -> (:Class)-[:CONTAINS]->
(:Method), and (:Function|:Method)-[:CALLS]->target.
"""

from pathlib import Path

from palimpsest.ir import Provenance, REPO, PACKAGE, FILE, CLASS, METHOD, FUNCTION
from palimpsest.extract.kotlin import extract

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

KOTLIN_SRC = """package app

fun main() {
    greet()
}

fun greet() {
}

class Service {
    fun handle() {
        greet()
    }
}
"""


def _extract(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="K")


def test_top_level_function_is_function_contained_by_file(tmp_path):
    ir = _extract(tmp_path, {"app.kt": KOTLIN_SRC})
    # top-level `fun main` / `fun greet` are Functions (NOT Methods), each grounded
    fn = ir.node("app.main()")
    assert fn is not None and fn.kind == FUNCTION
    assert fn.name == "main"
    assert fn.path == "app.kt"
    assert fn.start_line == 3  # `fun main() {`
    assert ir.node("app.greet()") is not None
    assert ir.node("app.greet()").kind == FUNCTION
    # (:File)-[:CONTAINS]->(:Function)
    assert ir.node("app.kt") is not None and ir.node("app.kt").kind == FILE
    assert ir.has_edge("CONTAINS", "app.kt", "app.main()")
    assert ir.has_edge("CONTAINS", "app.kt", "app.greet()")


def test_class_method_is_method_contained_by_class(tmp_path):
    ir = _extract(tmp_path, {"app.kt": KOTLIN_SRC})
    cls = ir.node("app.Service")
    assert cls is not None and cls.kind == CLASS
    # method shares the function_declaration node type but resolves to METHOD by
    # its class_body parent context — the load-bearing distinction of this slice.
    m = ir.node("app.Service#handle()")
    assert m is not None and m.kind == METHOD
    assert m.name == "handle"
    # (:File)-[:CONTAINS]->(:Class) and (:Class)-[:CONTAINS]->(:Method)
    assert ir.has_edge("CONTAINS", "app.kt", "app.Service")
    assert ir.has_edge("CONTAINS", "app.Service", "app.Service#handle()")
    # the method is NOT emitted as a top-level Function
    assert ir.node("app.handle()") is None


def test_calls_edges_name_based(tmp_path):
    ir = _extract(tmp_path, {"app.kt": KOTLIN_SRC})
    # (:Function)-[:CALLS]->(:Function): main() calls greet()
    assert ir.has_edge("CALLS", "app.main()", "app.greet()")
    # a method may also be the CALLS source: handle() calls greet()
    assert ir.has_edge("CALLS", "app.Service#handle()", "app.greet()")
    # no self-loops
    assert not any(e.src == e.dst for e in ir.edges_of("CALLS"))


def test_package_node_and_provenance(tmp_path):
    ir = _extract(tmp_path, {"app.kt": KOTLIN_SRC})
    pkg = ir.node("app")
    assert pkg is not None and pkg.kind == PACKAGE
    assert ir.nodes and ir.edges
    for n in ir.nodes:
        assert n.provenance == PROV
    for e in ir.edges:
        assert e.provenance == PROV


def test_kotlin_query_is_per_language_and_valid():
    # ADR-20260706 §결정6: the tags query lives as a separate per-language file the
    # extractor loads at runtime and that compiles against the Kotlin grammar.
    from tree_sitter import Language, Query
    import tree_sitter_kotlin as tskotlin
    from palimpsest.extract import kotlin as kmod

    qdir = Path(kmod.__file__).parent / "queries" / "kotlin"
    tags = qdir / "tags.scm"
    assert tags.is_file() and tags.read_text().strip()
    Query(Language(tskotlin.language()), tags.read_text())  # must compile
    assert kmod._QUERY_DIR == qdir


# ── is_test marker (issue #17: multilang test-impact) ──────────────────────────
# Kotlin has no import/annotation parsing in this slice, so the signal is the
# Gradle `src/test` path convention. Marks the file's code-unit nodes; production
# under src/main stays falsy. Pure PROPERTY, mirrors java.py.

_KT_TEST = "package app\n\nclass WidgetTest {\n    fun rendersDirectly() {\n        Widget().render()\n    }\n}\n"
_KT_PROD = "package app\n\nclass Widget {\n    fun render(): String {\n        return \"w\"\n    }\n}\n"


def test_is_test_marked_by_src_test_path(tmp_path):
    ir = _extract(tmp_path, {
        "src/test/kotlin/app/WidgetTest.kt": _KT_TEST,
        "src/main/kotlin/app/Widget.kt": _KT_PROD,
    })
    for n in ir.nodes:
        if n.path == "src/test/kotlin/app/WidgetTest.kt" and n.kind in (FILE, CLASS, METHOD, FUNCTION):
            assert n.is_test is True, (n.kind, n.qualified_name)
        if n.path == "src/main/kotlin/app/Widget.kt":
            assert not n.is_test, (n.kind, n.qualified_name)


def test_is_test_never_marks_repo_or_package(tmp_path):
    ir = _extract(tmp_path, {"src/test/kotlin/app/WidgetTest.kt": _KT_TEST})
    assert any(n.kind in (CLASS, METHOD) and n.is_test for n in ir.nodes)
    for n in ir.nodes:
        if n.kind in (REPO, PACKAGE):
            assert not n.is_test


def test_is_test_zero_misclassification_by_path(tmp_path):
    ir = _extract(tmp_path, {
        "src/test/kotlin/app/ATest.kt": _KT_TEST,
        "src/main/kotlin/app/Prod.kt": _KT_PROD,
    })
    parts_test = lambda p: any(
        seg == "src" and nxt == "test" for seg, nxt in zip(p.split("/"), p.split("/")[1:])
    )
    mis = [
        (n.kind, n.path, bool(n.is_test))
        for n in ir.nodes
        if n.kind in (FILE, CLASS, METHOD, FUNCTION) and bool(n.is_test) != parts_test(n.path)
    ]
    assert mis == [], mis


def test_is_test_marked_by_gradle_test_source_sets(tmp_path):
    # Gradle *Test source sets (Kotlin MPP / Android) are test code even though the
    # path is not `src/test`: commonTest / jvmTest / androidTest. `testFixtures` is a
    # fixtures source set, NOT a test source set — it stays production.
    ir = _extract(tmp_path, {
        "src/commonTest/kotlin/app/AT.kt": _KT_TEST,
        "src/jvmTest/kotlin/app/BT.kt": _KT_TEST,
        "src/androidTest/kotlin/app/CT.kt": _KT_TEST,
        "src/testFixtures/kotlin/app/Fix.kt": _KT_PROD,
        "src/main/kotlin/app/Prod.kt": _KT_PROD,
    })

    def is_test_set(p):
        parts = p.split("/")
        return any(a == "src" and (b == "test" or b.endswith("Test")) for a, b in zip(parts, parts[1:]))

    mis = [
        (n.kind, n.path, bool(n.is_test))
        for n in ir.nodes
        if n.kind in (FILE, CLASS, METHOD, FUNCTION) and bool(n.is_test) != is_test_set(n.path)
    ]
    assert mis == [], mis
    # explicit: testFixtures and main stay production
    for n in ir.nodes:
        if n.path and n.path.startswith(("src/testFixtures/", "src/main/")):
            assert not n.is_test, (n.path, n.kind)
