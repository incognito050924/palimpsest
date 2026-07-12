"""TDD for the Java static extractor (n2-impl-extract).

Hermetic: runs against a small real fixture copied under ``fixtures/`` (the
``commute`` feature slice), never the external corpus path.
"""

from pathlib import Path

import pytest

from palimpsest.ir import Provenance, PACKAGE, FILE, CLASS, METHOD
from palimpsest.extract import extract

FIXTURES = Path(__file__).parent / "fixtures"

# A fixed, deterministic provenance so extraction tests do not depend on git.
PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

PKG = "kr.co.ecoletree.service.commute.service"
IFACE = "kr.co.ecoletree.service.commute.service.CommuteService"
CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"


@pytest.fixture(scope="module")
def ir():
    return extract(FIXTURES, PROV, repo_name="EcoleTreeSystems")


def test_package_class_method_nodes_with_grounding(ir):
    # Package node exists with FQN identity
    pkg = ir.node(PKG)
    assert pkg is not None and pkg.kind == PACKAGE
    assert pkg.name == "service"

    # Class node: identity = package.Class
    cls = ir.node(IFACE)
    assert cls is not None and cls.kind == CLASS
    assert cls.name == "CommuteService"
    # file:line grounding
    assert cls.path == "src/main/java/kr/co/ecoletree/service/commute/service/CommuteService.java"
    assert cls.start_line == 7  # `public interface CommuteService {`
    assert cls.end_line >= cls.start_line

    # File node identity = repo-relative path
    f = ir.node(cls.path)
    assert f is not None and f.kind == FILE

    # Method node: identity = package.Class#method(paramTypes), overloads disambiguated
    m = ir.node(IFACE + "#insertGotoWork(Map,HttpServletRequest)")
    assert m is not None and m.kind == METHOD
    assert m.name == "insertGotoWork"
    assert m.path == cls.path
    assert m.start_line >= cls.start_line

    # CONTAINS hierarchy: Package->File, File->Class, Class->Method
    assert ir.has_edge("CONTAINS", PKG, cls.path)
    assert ir.has_edge("CONTAINS", cls.path, IFACE)
    assert ir.has_edge("CONTAINS", IFACE, m.qualified_name)


CTRL_FILE = "src/main/java/kr/co/ecoletree/service/commute/controller/CommuteController.java"


def test_imports_edges_resolve(ir):
    # External import: File -> java.util.Map (no node — honest dangling target)
    assert ir.has_edge("IMPORTS", CTRL_FILE, "java.util.Map")

    # Intra-corpus import resolves to an actual Class node
    assert ir.has_edge("IMPORTS", CTRL_FILE, IFACE)
    assert ir.node(IFACE) is not None  # target is a real node -> resolved


def test_depends_on_edge_from_field_type(ir):
    # CommuteController has `CommuteService service;` (field) and imports it ->
    # DEPENDS_ON Class->Class, both endpoints real nodes, no self-loop.
    assert ir.has_edge("DEPENDS_ON", CTRL, IFACE)
    assert not any(e.src == e.dst for e in ir.edges_of("DEPENDS_ON"))


def test_calls_edge_name_based(ir):
    # Controller body calls `service.selectAttedanceCondition(param)`. Name-based
    # match resolves to the CommuteService method (self-loop suppressed).
    src = CTRL + "#selectAttedanceCondition(Map,HttpServletRequest)"
    dst = IFACE + "#selectAttedanceCondition(Map)"
    assert ir.node(src) is not None and ir.node(dst) is not None
    assert ir.has_edge("CALLS", src, dst)
    assert not any(e.src == e.dst for e in ir.edges_of("CALLS"))


REPORT = "kr.co.ecoletree.service.report.service.ReportService"


def test_calls_precise_receiver_type_no_over_match(ir):
    # Controller field `service` is typed CommuteService; the body call
    # `service.selectCodeList(param)` must resolve ONLY to CommuteService's
    # method, never to the same-simple-named method on the unrelated
    # ReportService (name-based resolution over-matches both).
    caller = CTRL + "#selectCodeList(Map)"
    right = IFACE + "#selectCodeList(Map)"
    wrong = REPORT + "#selectCodeList(Map)"
    # all three nodes are real -> the collision is genuine
    assert ir.node(caller) is not None
    assert ir.node(right) is not None
    assert ir.node(wrong) is not None
    # precise edge kept
    assert ir.has_edge("CALLS", caller, right)
    # over-match edge to the unrelated type suppressed
    assert not ir.has_edge("CALLS", caller, wrong)


def _extract_ir(tmp_path, files):
    """Extract an IR from an inline {relpath: java-source} corpus."""
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="T")


def test_calls_interface_receiver_reaches_impl_not_unrelated(tmp_path):
    # receiver `svc` is typed by the interface Svc; the call must reach BOTH the
    # interface method AND the implementing class (test-impact reachability), but
    # NOT the coincidentally same-named method on the unrelated Other.
    ir = _extract_ir(tmp_path, {
        "Svc.java": "package p;\npublic interface Svc { String sel(String a); }\n",
        "SvcImpl.java": "package p;\npublic class SvcImpl implements Svc {\n"
                        "  public String sel(String a) { return a; }\n}\n",
        "Other.java": "package p;\npublic class Other { public String sel(String a) { return a; } }\n",
        "Ctrl.java": "package p;\npublic class Ctrl {\n  Svc svc;\n"
                     "  String run(String a) { return svc.sel(a); }\n}\n",
    })
    caller = "p.Ctrl#run(String)"
    assert ir.node(caller) is not None
    assert ir.has_edge("CALLS", caller, "p.Svc#sel(String)")
    assert ir.has_edge("CALLS", caller, "p.SvcImpl#sel(String)")
    assert not ir.has_edge("CALLS", caller, "p.Other#sel(String)")


def test_calls_inherited_method_excludes_unrelated_same_name(tmp_path):
    # d:Derived, foo() inherited from Base -> link Base#foo; the same-named method
    # on the unrelated class must NOT be linked (no name-based over-match on the
    # inherited path).
    ir = _extract_ir(tmp_path, {
        "Base.java": "package p;\npublic class Base { public String foo(String a) { return a; } }\n",
        "Derived.java": "package p;\npublic class Derived extends Base { }\n",
        "Unrelated.java": "package p;\npublic class Unrelated { public String foo(String a) { return a; } }\n",
        "Ctrl.java": "package p;\npublic class Ctrl {\n  Derived d;\n"
                     "  String run(String a) { return d.foo(a); }\n}\n",
    })
    caller = "p.Ctrl#run(String)"
    assert ir.node(caller) is not None
    assert ir.has_edge("CALLS", caller, "p.Base#foo(String)")
    assert not ir.has_edge("CALLS", caller, "p.Unrelated#foo(String)")


def test_calls_local_in_anonymous_class_does_not_shadow_field(tmp_path):
    # `thing` is a field typed Svc; an unrelated local `thing` typed Other lives in
    # an anonymous class declared INSIDE the method body (the common listener/Runnable
    # position). The inner local must not poison the field's type: `thing.sel(a)` in
    # call() must resolve to Svc, not Other.
    ir = _extract_ir(tmp_path, {
        "Svc.java": "package p;\npublic interface Svc { String sel(String a); }\n",
        "Other.java": "package p;\npublic class Other { public String sel(String a) { return a; } }\n",
        "Ctrl.java": (
            "package p;\n"
            "public class Ctrl {\n"
            "  Svc thing;\n"
            "  String call(String a) {\n"
            "    Runnable r = new Runnable() {\n"
            "      public void run() { Other thing = new Other(); thing.sel(\"x\"); }\n"
            "    };\n"
            "    return thing.sel(a);\n"
            "  }\n"
            "}\n"
        ),
    })
    caller = "p.Ctrl#call(String)"
    assert ir.node(caller) is not None
    assert ir.has_edge("CALLS", caller, "p.Svc#sel(String)")
    assert not ir.has_edge("CALLS", caller, "p.Other#sel(String)")


def test_calls_binding_in_nested_anonymous_member_type_does_not_leak(tmp_path):
    # Pathological inner-type position: a NAMED member type declared inside a
    # field-initializer anonymous class. Its field `thing:Other` must NOT leak into
    # the enclosing real class and shadow the real field `thing:Svc`.
    ir = _extract_ir(tmp_path, {
        "Svc.java": "package p;\npublic interface Svc { String sel(String a); }\n",
        "Other.java": "package p;\npublic class Other { public String sel(String a) { return a; } }\n",
        "Ctrl.java": (
            "package p;\n"
            "public class Ctrl {\n"
            "  Svc thing;\n"
            "  Object o = new Object() {\n"
            "    class Member { Other thing; void go() { thing.sel(\"x\"); } }\n"
            "  };\n"
            "  String call(String a) { return thing.sel(a); }\n"
            "}\n"
        ),
    })
    caller = "p.Ctrl#call(String)"
    assert ir.node(caller) is not None
    assert ir.has_edge("CALLS", caller, "p.Svc#sel(String)")
    assert not ir.has_edge("CALLS", caller, "p.Other#sel(String)")


def test_calls_queries_are_per_language_and_valid():
    # AC2: tags/locals queries live as separate per-language files the extractor
    # loads at runtime, and compile against the Java grammar (multi-language-ready).
    from tree_sitter import Language, Query
    import tree_sitter_java as tsjava
    from palimpsest.extract import java as jmod

    qdir = Path(jmod.__file__).parent / "queries" / "java"
    tags = qdir / "tags.scm"
    locals_ = qdir / "locals.scm"
    assert tags.is_file() and tags.read_text().strip()
    assert locals_.is_file() and locals_.read_text().strip()
    lang = Language(tsjava.language())
    Query(lang, tags.read_text())  # must compile against the grammar
    Query(lang, locals_.read_text())
    # the extractor loads these very files, not an inlined copy
    assert jmod._QUERY_DIR == qdir


def test_provenance_attached_to_every_node_and_edge(ir):
    assert ir.nodes and ir.edges
    for n in ir.nodes:
        assert n.provenance == PROV
    for e in ir.edges:
        assert e.provenance == PROV
    # serializable end to end
    d = ir.to_dict()
    assert d["nodes"][0]["provenance"]["source_commit"] == PROV.source_commit
    import json

    json.dumps(d)  # must not raise


def test_read_provenance_from_git(tmp_path):
    import subprocess

    from palimpsest.extract import read_provenance

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.name", "Fixture Author")
    git("config", "user.email", "fix@example.com")
    (tmp_path / "A.java").write_text("package p; class A {}\n")
    git("add", "A.java")
    git("commit", "-q", "-m", "seed")

    head = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    prov = read_provenance(tmp_path)
    assert prov.source_commit == head
    assert prov.author == "Fixture Author <fix@example.com>"
    assert prov.committed_at  # ISO-8601 string present
