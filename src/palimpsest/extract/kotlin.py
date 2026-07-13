"""Static extraction of Kotlin source into the palimpsest IR.

Parser: tree-sitter-kotlin (py-tree-sitter). Deterministic structural ontology
only, mirroring the Java extractor (ADR-20260706 §결정6: per-language
``queries/<lang>/*.scm`` own the build-less tree-sitter spine, the resolver stays
language-neutral).

Kotlin has BOTH top-level functions and class methods, and they share ONE grammar
node (``function_declaration``). They are told apart ONLY by parent context: a
``function_declaration`` directly under ``source_file`` is a top-level Function;
one under ``class_body`` is a Method. CALLS is resolved name-based for this first
slice — receiver typing (as the Java extractor does) is out of scope here.

Identity (``qualified_name``) mirrors the Java scheme, minimally adapted:
  - Function : package.name(paramTypes)   (no declaring class)
  - Class    : package.Class
  - Method   : package.Class#name(paramTypes)
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import tree_sitter_kotlin as tskotlin
from tree_sitter import Language, Parser, Query, QueryCursor, Node as TSNode

from palimpsest.ir import IR, Node, Edge, Provenance
from palimpsest.ir import REPO, PACKAGE, FILE, CLASS, METHOD, FUNCTION
from palimpsest.ir import CONTAINS, CALLS

_LANGUAGE = Language(tskotlin.language())

# Per-language tree-sitter query (ADR-20260706 §결정6): `tags.scm` yields the call
# references the name-based resolver consumes. A new language plugs in by adding
# its own queries/<lang>/*.scm; the resolver below stays language-agnostic.
_QUERY_DIR = Path(__file__).parent / "queries" / "kotlin"
_TAGS_QUERY = Query(_LANGUAGE, (_QUERY_DIR / "tags.scm").read_text())


def _parser() -> Parser:
    return Parser(_LANGUAGE)


def _line(point) -> int:
    return point[0] + 1


def _name_of(node: TSNode) -> str | None:
    """The declared name: the first ``identifier`` direct child of a decl node."""
    for c in node.named_children:
        if c.type == "identifier":
            return c.text.decode()
    return None


def _type_simple(node: TSNode | None) -> str:
    """Base (unqualified, un-generic, non-null) type name, or ``?`` when absent."""
    if node is None:
        return "?"
    txt = node.text.decode().split("<")[0].replace("?", "").strip()
    return txt.split(".")[-1] or "?"


def _param_types(func: TSNode) -> list[str]:
    """Simple type names of ``func``'s value parameters (positional, for identity).

    A ``parameter`` node is ``(identifier <name>) (<type>)``; the first identifier
    is the parameter name, the following node is its type.
    """
    params: TSNode | None = None
    for c in func.named_children:
        if c.type == "function_value_parameters":
            params = c
            break
    if params is None:
        return []
    out: list[str] = []
    for p in params.named_children:
        if p.type != "parameter":
            continue
        type_node = None
        for c in p.named_children:
            if c.type != "identifier":  # first identifier is the param name
                type_node = c
                break
        out.append(_type_simple(type_node))
    return out


def _package_fqn(root: TSNode) -> str:
    for c in root.named_children:
        if c.type == "package_header":
            for cc in c.named_children:
                if cc.type == "qualified_identifier":
                    return cc.text.decode()
    return ""


def _is_test_path(rel_path: str) -> bool:
    """Gradle/Maven test-source convention: a ``src/<test-source-set>`` segment pair.
    The source set is ``test`` (standard) or ends in ``Test`` (Gradle ``commonTest`` /
    ``jvmTest`` / ``androidTest`` for Kotlin MPP / Android); ``testFixtures`` is a
    fixtures source set and stays production."""
    parts = rel_path.split("/")
    return any(a == "src" and (b == "test" or b.endswith("Test")) for a, b in zip(parts, parts[1:]))


class _FileWalker:
    """Collects structural nodes + CONTAINS edges for a single parsed Kotlin file."""

    def __init__(self, rel_path: str, source: bytes, root: TSNode, prov: Provenance):
        self.rel_path = rel_path
        self.source = source
        self.root = root
        self.prov = prov
        self.pkg = _package_fqn(root)
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []

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
        if self.pkg:
            self._edge(CONTAINS, self.pkg, self.rel_path)
        # A function_declaration directly under source_file is a top-level Function;
        # a class_declaration nests its methods (parent-context discrimination).
        for child in self.root.named_children:
            if child.type == "function_declaration":
                self._function_decl(child)
            elif child.type == "class_declaration":
                self._class_decl(child)
        # is_test marker (issue #17): Kotlin parses no imports/annotations here, so
        # the signal is the Gradle `src/test` path convention. Marks every code-unit
        # node this file produced — pure PROPERTY, post-walk (mirrors java.py).
        if _is_test_path(self.rel_path):
            for n in self.nodes:
                if n.kind in (FILE, CLASS, METHOD, FUNCTION):
                    n.is_test = True

    def _function_decl(self, node: TSNode) -> None:
        name = _name_of(node)
        if name is None:
            return
        fqn = self._callable_fqn(name, _param_types(node), class_fqn=None)
        self.nodes.append(
            Node(
                kind=FUNCTION,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=_line(node.start_point),
                end_line=_line(node.end_point),
            )
        )
        self._edge(CONTAINS, self.rel_path, fqn)

    def _class_decl(self, node: TSNode) -> None:
        name = _name_of(node)
        if name is None:
            return
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
        body = None
        for c in node.named_children:
            if c.type == "class_body":
                body = c
                break
        if body is None:
            return
        for member in body.named_children:
            if member.type == "function_declaration":
                self._method_decl(member, class_fqn=fqn)

    def _method_decl(self, node: TSNode, class_fqn: str) -> None:
        name = _name_of(node)
        if name is None:
            return
        fqn = self._callable_fqn(name, _param_types(node), class_fqn=class_fqn)
        self.nodes.append(
            Node(
                kind=METHOD,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=_line(node.start_point),
                end_line=_line(node.end_point),
            )
        )
        self._edge(CONTAINS, class_fqn, fqn)


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


def _calls_edges(nodes: list[Node], call_sites: list[CallSite], prov: Provenance) -> list[Edge]:
    """CALLS (Function|Method -> Function|Method) resolved name-based for this slice.

    The enclosing callable (source) is the innermost Function/Method line-range
    containing the call site; the target is every callable sharing the callee's
    simple name. Self-loops are suppressed. Receiver typing is out of scope.
    """
    by_name: dict[str, list[str]] = defaultdict(list)
    ranges: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for n in nodes:
        if n.kind in (FUNCTION, METHOD):
            by_name[n.name].append(n.qualified_name)
            ranges[n.path].append((n.start_line, n.end_line, n.qualified_name))

    seen: set[tuple[str, str]] = set()
    out: list[Edge] = []
    for path, line, name in call_sites:
        src = _innermost(ranges.get(path, []), line)
        if src is None:
            continue
        for dst in by_name.get(name, ()):
            if dst == src or (src, dst) in seen:
                continue
            seen.add((src, dst))
            out.append(Edge(kind=CALLS, src=src, dst=dst, provenance=prov))
    return out


def _iter_kotlin_files(root: Path):
    for p in sorted(root.rglob("*.kt")):
        if p.is_file():
            yield p


def extract(root: Path | str, provenance: Provenance, repo_name: str | None = None) -> IR:
    """Parse every ``*.kt`` file under ``root`` into an :class:`IR`.

    ``root`` is treated as the repository root; File node paths are recorded
    repo-relative to it. Every node and edge carries ``provenance``.
    """
    root = Path(root)
    repo_name = repo_name or root.name
    parser = _parser()

    nodes: list[Node] = []
    edges: list[Edge] = []
    packages: set[str] = set()
    call_sites: list[CallSite] = []

    for path in _iter_kotlin_files(root):
        source = path.read_bytes()
        tree = parser.parse(source)
        rel = path.relative_to(root).as_posix()
        walker = _FileWalker(rel, source, tree.root_node, provenance)
        walker.run()
        nodes.extend(walker.nodes)
        edges.extend(walker.edges)
        if walker.pkg:
            packages.add(walker.pkg)
        call_sites.extend(_scan_calls(rel, tree.root_node))

    edges.extend(_calls_edges(nodes, call_sites, provenance))

    repo = Node(kind=REPO, qualified_name=repo_name, name=repo_name, provenance=provenance)
    repo_and_pkgs: list[Node] = [repo]
    repo_edges: list[Edge] = []
    for pkg in sorted(packages):
        repo_and_pkgs.append(
            Node(
                kind=PACKAGE,
                qualified_name=pkg,
                name=pkg.rsplit(".", 1)[-1],
                provenance=provenance,
            )
        )
        repo_edges.append(Edge(kind=CONTAINS, src=repo_name, dst=pkg, provenance=provenance))

    return IR(nodes=repo_and_pkgs + nodes, edges=repo_edges + edges)
