"""Static extraction of Python source into the palimpsest IR.

Parser: tree-sitter-python (py-tree-sitter). Deterministic structural ontology
only, mirroring the Kotlin/Java extractors (ADR-20260706 §결정6: per-language
``queries/<lang>/*.scm`` own the build-less tree-sitter spine, the resolver stays
language-neutral).

Python has NO in-source package header, and a class body and a function body are
the SAME grammar node (``block``). So — like Kotlin's shared ``function_declaration``
— a bare ``function_definition`` cannot be told apart by node type alone; parent
CONTEXT is the only discriminator, done structurally in the walker:

  - ``function_definition`` directly under ``module``      -> Function (File CONTAINS)
  - ``function_definition`` directly under a class ``block`` -> Method  (Class CONTAINS)
  - ``function_definition`` inside a function body          -> NOT emitted
    (a nested/local def is attributed to its enclosing callable, never surfaced)

Identity (``qualified_name``), package prefix empty for Python:
  - Function : name(paramNames)          (no declaring class)
  - Class    : Class
  - Method   : Class#name(paramNames)
  - Variable : name                      (module-level binding, name only)

Decorators (ac-4) are emitted STRUCTURALLY from the ``decorated_definition`` node,
not via the name-based call scan: a plain ``@deco`` token is not a ``call`` node and
sits on a line ABOVE the callable's ``def`` — so line-range attribution (_innermost)
would either miss it or mis-attribute it. Emitting ``CALLS decorated_callable ->
decorator`` here pins the source to the decorated callable directly. An external
decorator (``@dataclass``/``@property``/...) resolves to no in-corpus callable and
is honestly dropped (no phantom node).
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Query, QueryCursor, Node as TSNode

from palimpsest.ir import IR, Node, Edge, Provenance
from palimpsest.ir import REPO, FILE, CLASS, METHOD, FUNCTION, VARIABLE
from palimpsest.ir import CONTAINS, CALLS, IMPORTS

_LANGUAGE = Language(tspython.language())

# Per-language tree-sitter query (ADR-20260706 §결정6): `tags.scm` yields the call
# references the name-based resolver consumes. A new language plugs in by adding
# its own queries/<lang>/*.scm; the resolver below stays language-agnostic.
_QUERY_DIR = Path(__file__).parent / "queries" / "python"
_TAGS_QUERY = Query(_LANGUAGE, (_QUERY_DIR / "tags.scm").read_text())

# Virtualenv / build / cache dirs the file iterator must NOT descend into: a naive
# ``rglob("*.py")`` would drag third-party site-packages (and stale build output)
# into the corpus. Pruned at walk time (coverage constraint).
_IGNORE_DIRS = frozenset(
    {".venv", "venv", "site-packages", "build", ".tox", "__pycache__", ".git", "dist", ".eggs"}
)


def _parser() -> Parser:
    return Parser(_LANGUAGE)


def _line(point) -> int:
    return point[0] + 1


def _first_identifier(node: TSNode) -> str | None:
    """The first ``identifier`` in ``node``'s subtree (a parameter's declared name)."""
    if node.type == "identifier":
        return node.text.decode()
    for c in node.named_children:
        r = _first_identifier(c)
        if r is not None:
            return r
    return None


def _param_names(func: TSNode) -> list[str]:
    """Positional parameter names of ``func`` (for the identity's ``(...)`` suffix).

    Python parameters are rarely typed, so — unlike the Java/Kotlin type-based
    identity — Python uses parameter NAMES. Separators (``*`` / ``/``) carry no
    identifier and are skipped.
    """
    params = func.child_by_field_name("parameters")
    if params is None:
        return []
    out: list[str] = []
    for p in params.named_children:
        nm = _first_identifier(p)
        if nm is not None:
            out.append(nm)
    return out


def _callee_name(expr: TSNode | None) -> str | None:
    """Simple callee name of a call/decorator expression: ``f`` / ``a.b.f`` / ``f(...)``."""
    if expr is None:
        return None
    if expr.type == "identifier":
        return expr.text.decode()
    if expr.type == "attribute":
        attr = expr.child_by_field_name("attribute")
        return attr.text.decode() if attr is not None else None
    if expr.type == "call":
        return _callee_name(expr.child_by_field_name("function"))
    return None


def _decorator_name(dec: TSNode) -> str | None:
    """The decorator's callable simple name (``@deco`` / ``@a.deco`` / ``@deco(...)``)."""
    for c in dec.named_children:
        return _callee_name(c)
    return None


def _dotted_target(node: TSNode) -> str | None:
    """The dotted module path of an import clause (``dotted_name`` or ``aliased_import``)."""
    if node.type == "dotted_name":
        return node.text.decode()
    if node.type == "aliased_import":
        for c in node.named_children:
            if c.type == "dotted_name":
                return c.text.decode()
    return None


class _FileWalker:
    """Collects structural nodes + CONTAINS/IMPORTS edges for one parsed Python file.

    Decorator CALLS are collected as ``(decorated_callable_fqn, decorator_name)``
    pairs (the source fqn is already known structurally); their dst is resolved
    name-based alongside the scanned call sites.
    """

    def __init__(self, rel_path: str, source: bytes, root: TSNode, prov: Provenance):
        self.rel_path = rel_path
        self.source = source
        self.root = root
        self.prov = prov
        self.pkg = ""  # Python has no in-source package header
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.decorator_calls: list[tuple[str, str]] = []
        self.seen_vars: set[str] = set()

    def _edge(self, kind: str, src: str, dst: str) -> None:
        self.edges.append(Edge(kind=kind, src=src, dst=dst, provenance=self.prov))

    def _callable_fqn(self, name: str, params: list[str], class_fqn: str | None) -> str:
        joined = ",".join(params)
        if class_fqn is not None:
            return f"{class_fqn}#{name}({joined})"
        prefix = f"{self.pkg}." if self.pkg else ""
        return f"{prefix}{name}({joined})"

    def run(self) -> None:
        total_lines = self.source.count(b"\n") + 1
        self.nodes.append(
            Node(
                kind=FILE,
                qualified_name=self.rel_path,
                name=Path(self.rel_path).name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=1,
                end_line=total_lines,
            )
        )
        # Only MODULE-DIRECT children are structural surface: a def under `module`
        # is a Function, a class nests its methods, an assignment is a Variable, an
        # import is an IMPORTS edge. Nested defs (inside a body) are never visited.
        for child in self.root.named_children:
            if child.type == "function_definition":
                self._function_def(child, class_fqn=None)
            elif child.type == "class_definition":
                self._class_def(child)
            elif child.type == "decorated_definition":
                self._decorated(child, class_fqn=None)
            elif child.type == "expression_statement":
                self._maybe_variable(child)
            elif child.type in ("import_statement", "import_from_statement"):
                self._import(child)

    def _function_def(self, node: TSNode, class_fqn: str | None) -> str | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        name = name_node.text.decode()
        fqn = self._callable_fqn(name, _param_names(node), class_fqn)
        self.nodes.append(
            Node(
                kind=METHOD if class_fqn is not None else FUNCTION,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=_line(node.start_point),
                end_line=_line(node.end_point),
            )
        )
        self._edge(CONTAINS, class_fqn if class_fqn is not None else self.rel_path, fqn)
        return fqn

    def _class_def(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        fqn = f"{self.pkg}.{name}" if self.pkg else name
        self.nodes.append(
            Node(
                kind=CLASS,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=_line(node.start_point),
                end_line=_line(node.end_point),
            )
        )
        self._edge(CONTAINS, self.rel_path, fqn)
        body = node.child_by_field_name("body")
        if body is None:
            return
        # Class-block-direct defs are Methods; a decorated one keeps its structural
        # decorator CALLS. Nested classes / local defs are out of scope for v1.
        for member in body.named_children:
            if member.type == "function_definition":
                self._function_def(member, class_fqn=fqn)
            elif member.type == "decorated_definition":
                self._decorated(member, class_fqn=fqn)

    def _decorated(self, node: TSNode, class_fqn: str | None) -> None:
        inner = node.child_by_field_name("definition")
        if inner is None:
            return
        if inner.type == "function_definition":
            fqn = self._function_def(inner, class_fqn)
            if fqn is None:
                return
            # CALLS decorated_callable -> decorator, emitted structurally so the
            # source is the decorated callable itself (not a line-range guess).
            for dec in node.named_children:
                if dec.type == "decorator":
                    dname = _decorator_name(dec)
                    if dname:
                        self.decorator_calls.append((fqn, dname))
        elif inner.type == "class_definition":
            # A decorated class is emitted structurally; a class is not a CALLS
            # source in this ontology (CALLS is Function|Method -> Function|Method).
            self._class_def(inner)

    def _maybe_variable(self, stmt: TSNode) -> None:
        for c in stmt.named_children:
            if c.type != "assignment":
                continue
            left = c.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                self._variable(left.text.decode(), left)

    def _variable(self, name: str, at: TSNode) -> None:
        fqn = f"{self.pkg}.{name}" if self.pkg else name
        if fqn in self.seen_vars:  # re-assignment of an already-bound name -> dedup
            return
        self.seen_vars.add(fqn)
        self.nodes.append(
            Node(
                kind=VARIABLE,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=_line(at.start_point),
                end_line=_line(at.end_point),
            )
        )
        self._edge(CONTAINS, self.rel_path, fqn)

    def _import(self, node: TSNode) -> None:
        # File IMPORTS the referenced module. External/unresolved modules get NO
        # node (honest external — the edge dangles, no phantom is fabricated).
        if node.type == "import_statement":
            for c in node.named_children:
                module = _dotted_target(c)
                if module:
                    self._edge(IMPORTS, self.rel_path, module)
        elif node.type == "import_from_statement":
            mod = node.child_by_field_name("module_name")
            if mod is not None and mod.type == "dotted_name":
                self._edge(IMPORTS, self.rel_path, mod.text.decode())


# A resolved call site: (rel_path, call_line, callee_name).
CallSite = tuple[str, int, str]


def _scan_calls(rel_path: str, root: TSNode) -> list[CallSite]:
    """Run the per-language tags query over one file's tree for call references."""
    calls: list[CallSite] = []
    for _pat, caps in QueryCursor(_TAGS_QUERY).matches(root):
        names = caps.get("reference.call.name")
        if not names:  # a definition match, not a call reference
            continue
        call_node = caps["reference.call"][0]
        calls.append((rel_path, call_node.start_point[0] + 1, names[0].text.decode()))
    return calls


def _innermost(ranges: list[tuple[int, int, str]], line: int) -> str | None:
    """FQN of the smallest [start, end] range containing ``line`` (innermost node)."""
    best: str | None = None
    best_span = -1
    for start, end, fqn in ranges:
        if start <= line <= end:
            span = end - start
            if best is None or span < best_span:
                best, best_span = fqn, span
    return best


def _calls_edges(
    nodes: list[Node],
    call_sites: list[CallSite],
    decorator_calls: list[tuple[str, str]],
    prov: Provenance,
) -> list[Edge]:
    """CALLS (Function|Method -> Function|Method) resolved name-based for this slice.

    For a scanned call site the enclosing callable (source) is the innermost
    Function/Method line-range containing the call; for a decorator the source is
    already the decorated callable. The target is every callable sharing the
    callee's simple name. Self-loops are suppressed; edges dedup by (src, dst).
    """
    by_name: dict[str, list[str]] = defaultdict(list)
    ranges: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for n in nodes:
        if n.kind in (FUNCTION, METHOD):
            by_name[n.name].append(n.qualified_name)
            ranges[n.path].append((n.start_line, n.end_line, n.qualified_name))

    seen: set[tuple[str, str]] = set()
    out: list[Edge] = []

    def _emit(src: str | None, name: str) -> None:
        if src is None:
            return
        for dst in by_name.get(name, ()):
            if dst == src or (src, dst) in seen:
                continue
            seen.add((src, dst))
            out.append(Edge(kind=CALLS, src=src, dst=dst, provenance=prov))

    for path, line, name in call_sites:
        _emit(_innermost(ranges.get(path, []), line), name)
    for src_fqn, name in decorator_calls:
        _emit(src_fqn, name)
    return out


def _iter_python_files(root: Path):
    """Yield ``*.py`` files under ``root``, PRUNING virtualenv/build/cache dirs.

    Uses ``os.walk`` with in-place ``dirnames`` pruning so an ignored directory is
    never descended into (not a post-filtered ``rglob``) — third-party
    site-packages must not leak into the corpus.
    """
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(Path(dirpath) / fn)
    return sorted(out)


def extract(root: Path | str, provenance: Provenance, repo_name: str | None = None) -> IR:
    """Parse every ``*.py`` file under ``root`` into an :class:`IR`.

    ``root`` is treated as the repository root; File node paths are recorded
    repo-relative to it. Every node and edge carries ``provenance``.
    """
    root = Path(root)
    repo_name = repo_name or root.name
    parser = _parser()

    nodes: list[Node] = []
    edges: list[Edge] = []
    call_sites: list[CallSite] = []
    decorator_calls: list[tuple[str, str]] = []

    for path in _iter_python_files(root):
        source = path.read_bytes()
        tree = parser.parse(source)
        rel = path.relative_to(root).as_posix()
        walker = _FileWalker(rel, source, tree.root_node, provenance)
        walker.run()
        nodes.extend(walker.nodes)
        edges.extend(walker.edges)
        decorator_calls.extend(walker.decorator_calls)
        call_sites.extend(_scan_calls(rel, tree.root_node))

    edges.extend(_calls_edges(nodes, call_sites, decorator_calls, provenance))

    repo = Node(kind=REPO, qualified_name=repo_name, name=repo_name, provenance=provenance)
    return IR(nodes=[repo] + nodes, edges=edges)
