"""TDD for the Python static extractor (n-impl-python-extract).

Hermetic: extracts an IR from an inline ``{relpath: python-source}`` corpus written
under ``tmp_path`` — never an external path. Mirrors the Kotlin/Java extractor
contract (ADR-20260706 §결정6: per-language queries/<lang>/*.scm, language-neutral
resolver).

The load-bearing Python facts these tests pin:
  - a module-direct ``function_definition`` is a Function; a class-block-direct one
    is a Method; a def inside a function body is NOT emitted (enclosing attribution).
    Parent context is the only discriminator — Python has no package header and the
    class/function body share ONE grammar node (``block``). (ac-1)
  - CALLS is name-based, round-trips, Function AND Method are both sources, no
    self-loops. (ac-2)
  - File IMPORTS the referenced module; external modules get no phantom node. (ac-3)
  - a decorator emits CALLS decorated_callable -> decorator STRUCTURALLY (the token
    sits above the callable's def line); an external decorator honestly drops. (ac-4)
  - a module-direct assignment target is a Variable (name only, re-assignment
    deduped); def/class are naturally excluded. (ac-5)
  - the file iterator PRUNES .venv/build/cache dirs (coverage constraint).
"""

from pathlib import Path

from palimpsest.ir import Provenance, FILE, CLASS, METHOD, FUNCTION, VARIABLE
from palimpsest.extract.python import extract

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

PY_SRC = """import os
from collections import defaultdict

CONFIG = {}
CONFIG = {"a": 1}
THRESHOLD = 10


def register(fn):
    return fn


def main():
    greet()
    helper()


def greet():
    pass


@register
def decorated_fn():
    pass


@dataclass
class Service:
    def handle(self):
        greet()

    @property
    def label(self):
        return "s"


def helper():
    def local_nested():
        pass
    local_nested()
"""


def _extract(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="P")


def _def_line(prefix: str) -> int:
    return next(i for i, l in enumerate(PY_SRC.splitlines(), 1) if l.startswith(prefix))


# --- ac-1: parent-context discrimination (Function vs Method vs unemitted local) ---


def test_module_function_is_function_contained_by_file(tmp_path):
    ir = _extract(tmp_path, {"app.py": PY_SRC})
    fn = ir.node("main()")
    assert fn is not None and fn.kind == FUNCTION
    assert fn.name == "main"
    assert fn.path == "app.py"
    assert fn.start_line == _def_line("def main")  # the `def main():` line, not a decorator
    assert ir.node("greet()") is not None and ir.node("greet()").kind == FUNCTION
    assert ir.node("register(fn)") is not None and ir.node("register(fn)").kind == FUNCTION
    # (:File)-[:CONTAINS]->(:Function)
    assert ir.node("app.py") is not None and ir.node("app.py").kind == FILE
    assert ir.has_edge("CONTAINS", "app.py", "main()")
    assert ir.has_edge("CONTAINS", "app.py", "greet()")


def test_class_method_is_method_contained_by_class(tmp_path):
    ir = _extract(tmp_path, {"app.py": PY_SRC})
    cls = ir.node("Service")
    assert cls is not None and cls.kind == CLASS
    # `handle` shares the function_definition node type but resolves to METHOD by
    # its class-block parent context — the load-bearing distinction of this slice.
    m = ir.node("Service#handle(self)")
    assert m is not None and m.kind == METHOD
    assert m.name == "handle"
    assert ir.has_edge("CONTAINS", "app.py", "Service")
    assert ir.has_edge("CONTAINS", "Service", "Service#handle(self)")
    # the method is NOT emitted as a module-level Function
    assert ir.node("handle(self)") is None


def test_nested_local_def_is_not_emitted(tmp_path):
    ir = _extract(tmp_path, {"app.py": PY_SRC})
    # a def inside a function body is attributed to its enclosing callable, never surfaced
    assert ir.node("local_nested()") is None
    assert not any(n.name == "local_nested" for n in ir.nodes)


# --- ac-2: CALLS name-based, both Function and Method as source, no self-loops ---


def test_calls_edges_name_based(tmp_path):
    ir = _extract(tmp_path, {"app.py": PY_SRC})
    # (:Function)-[:CALLS]->(:Function): main() calls greet() and helper()
    assert ir.has_edge("CALLS", "main()", "greet()")
    assert ir.has_edge("CALLS", "main()", "helper()")
    # a method is also a CALLS source: handle() calls greet()
    assert ir.has_edge("CALLS", "Service#handle(self)", "greet()")
    # no self-loops
    assert not any(e.src == e.dst for e in ir.edges_of("CALLS"))


# --- ac-3: File IMPORTS referenced module, external = honest, no phantom node ---


def test_imports_edges_and_honest_external(tmp_path):
    ir = _extract(tmp_path, {"app.py": PY_SRC})
    assert ir.has_edge("IMPORTS", "app.py", "os")               # import os
    assert ir.has_edge("IMPORTS", "app.py", "collections")      # from collections import ...
    # external unresolved modules get NO node (phantom-free)
    assert ir.node("os") is None
    assert ir.node("collections") is None


# --- ac-4: decorator CALLS emitted structurally; external decorator drops ---


def test_decorator_in_corpus_lands_and_external_drops(tmp_path):
    ir = _extract(tmp_path, {"app.py": PY_SRC})
    # decorated function is still emitted structurally despite the decorator
    assert ir.node("decorated_fn()") is not None and ir.node("decorated_fn()").kind == FUNCTION
    # in-corpus decorator: CALLS decorated_callable -> decorator (register is a Function)
    assert ir.has_edge("CALLS", "decorated_fn()", "register(fn)")
    # external decorators (@dataclass on the class, @property on the method) resolve
    # to no in-corpus callable and are honestly dropped — no phantom, no edge
    assert ir.node("Service") is not None
    assert ir.node("Service#label(self)") is not None and ir.node("Service#label(self)").kind == METHOD
    assert not any("dataclass" in e.dst for e in ir.edges_of("CALLS"))
    assert not any("property" in e.dst for e in ir.edges_of("CALLS"))


# --- ac-5: module-direct assignment -> Variable (name only, deduped) ---


def test_module_assignment_is_variable_deduped(tmp_path):
    ir = _extract(tmp_path, {"app.py": PY_SRC})
    cfg = ir.node("CONFIG")
    assert cfg is not None and cfg.kind == VARIABLE and cfg.name == "CONFIG"
    assert ir.node("THRESHOLD") is not None and ir.node("THRESHOLD").kind == VARIABLE
    assert ir.has_edge("CONTAINS", "app.py", "CONFIG")
    assert ir.has_edge("CONTAINS", "app.py", "THRESHOLD")
    # re-assignment (CONFIG assigned twice) yields exactly ONE Variable node
    assert sum(1 for n in ir.nodes if n.kind == VARIABLE and n.name == "CONFIG") == 1
    # def / class / decorated_definition are NOT variables
    var_names = {n.name for n in ir.nodes if n.kind == VARIABLE}
    assert var_names == {"CONFIG", "THRESHOLD"}


# --- coverage constraint: ignore-dir guard on the file iterator ---


def test_iterator_prunes_ignored_dirs(tmp_path):
    ir = _extract(
        tmp_path,
        {
            "app.py": "def real_fn():\n    pass\n",
            ".venv/junk.py": "def should_not_appear():\n    pass\n",
            "build/generated.py": "def also_hidden():\n    pass\n",
        },
    )
    assert ir.node("real_fn()") is not None
    assert ir.node("should_not_appear()") is None
    assert ir.node("also_hidden()") is None
    assert not any(n.name in ("should_not_appear", "also_hidden") for n in ir.nodes)


# --- provenance stamped on every node/edge ---


def test_provenance_on_all_nodes_and_edges(tmp_path):
    ir = _extract(tmp_path, {"app.py": PY_SRC})
    assert ir.nodes and ir.edges
    for n in ir.nodes:
        assert n.provenance == PROV
    for e in ir.edges:
        assert e.provenance == PROV


# --- ADR-20260706 §결정6: per-language tags query compiles against the grammar ---


def test_python_query_is_per_language_and_valid():
    from tree_sitter import Language, Query
    import tree_sitter_python as tspython
    from palimpsest.extract import python as pymod

    qdir = Path(pymod.__file__).parent / "queries" / "python"
    tags = qdir / "tags.scm"
    assert tags.is_file() and tags.read_text().strip()
    Query(Language(tspython.language()), tags.read_text())  # must compile
    assert pymod._QUERY_DIR == qdir
