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
