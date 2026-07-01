"""Static extraction of Java source into the palimpsest IR.

Parser: tree-sitter-java (py-tree-sitter). Deterministic structural ontology only;
CALLS is name-based best-effort (no full type resolution) and Lombok-generated
members are invisible to a source parser — both acceptable for v1.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Node as TSNode

from palimpsest.ir import IR, Node, Edge, Provenance
from palimpsest.ir import REPO, PACKAGE, FILE, CLASS, METHOD
from palimpsest.ir import CONTAINS, IMPORTS, CALLS, DEPENDS_ON

_TYPE_DECLS = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration")
_METHOD_DECLS = ("method_declaration", "constructor_declaration")

_LANGUAGE = Language(tsjava.language())


def _parser() -> Parser:
    return Parser(_LANGUAGE)


def _line(point) -> int:
    return point[0] + 1


def _simple_type_name(node: TSNode | None) -> str | None:
    """Base (unqualified, un-generic) reference-type name, or None for primitives."""
    if node is None:
        return None
    t = node.type
    if t == "generic_type":
        return _simple_type_name(node.named_children[0]) if node.named_children else None
    if t == "scoped_type_identifier":
        return node.text.decode().split("<")[0].split(".")[-1]
    if t == "type_identifier":
        return node.text.decode()
    if t == "array_type":
        return _simple_type_name(node.child_by_field_name("element"))
    if t == "annotated_type":
        for c in reversed(node.named_children):
            r = _simple_type_name(c)
            if r:
                return r
        return None
    # void_type / boolean_type / integral_type / floating_point_type / ...
    return None


def _param_types(method: TSNode) -> list[str]:
    params = method.child_by_field_name("parameters")
    out: list[str] = []
    if params is None:
        return out
    for fp in params.named_children:
        if fp.type not in ("formal_parameter", "spread_parameter"):
            continue
        t = fp.child_by_field_name("type")
        name = _simple_type_name(t)
        if name is None and t is not None:
            name = t.text.decode()  # primitive (boolean, int, void, ...)
        out.append(name or "?")
    return out


def _package_fqn(root: TSNode) -> str:
    for c in root.named_children:
        if c.type == "package_declaration":
            return c.named_children[0].text.decode()
    return ""


class _FileWalker:
    """Collects nodes + edges for a single parsed Java file."""

    def __init__(self, rel_path: str, source: bytes, root: TSNode, prov: Provenance):
        self.rel_path = rel_path
        self.source = source
        self.root = root
        self.prov = prov
        self.pkg = _package_fqn(root)
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        # class fqn -> tree-sitter body node (for later edge slices)
        self.class_bodies: dict[str, TSNode] = {}
        # class fqn -> referenced simple type names (fields + params + imports)
        self.class_refs: dict[str, set[str]] = {}
        # simple names of single-type imports in this file (attributed to its classes)
        self.import_simple: set[str] = set()
        self.file_classes: list[str] = []
        # method fqn -> simple names invoked in its body (for name-based CALLS)
        self.method_calls: dict[str, set[str]] = {}

    def _edge(self, kind: str, src: str, dst: str) -> None:
        self.edges.append(Edge(kind=kind, src=src, dst=dst, provenance=self.prov))

    def run(self) -> None:
        total_lines = self.source.count(b"\n") + 1
        file_node = Node(
            kind=FILE,
            qualified_name=self.rel_path,
            name=Path(self.rel_path).name,
            provenance=self.prov,
            path=self.rel_path,
            start_line=1,
            end_line=total_lines,
        )
        self.nodes.append(file_node)
        # Package -> File
        if self.pkg:
            self._edge(CONTAINS, self.pkg, self.rel_path)
        # imports + top-level type declarations
        for child in self.root.named_children:
            if child.type == "import_declaration":
                self._import_decl(child)
            elif child.type in _TYPE_DECLS:
                self._type_decl(child, enclosing_fqn=None, container_id=self.rel_path)
        # imported types count as dependencies of every class declared in the file
        for fqn in self.file_classes:
            self.class_refs[fqn].update(self.import_simple)

    def _import_decl(self, node: TSNode) -> None:
        target = None
        wildcard = any(c.type == "asterisk" for c in node.children)
        for c in node.named_children:
            if c.type in ("scoped_identifier", "identifier"):
                target = c.text.decode()
        if target:
            # File IMPORTS the referenced qualified name (a Class for single-type
            # imports, a Package for `a.b.*`). Resolution is by node-id match.
            self._edge(IMPORTS, self.rel_path, target)
            if not wildcard:
                self.import_simple.add(target.split(".")[-1])

    def _type_decl(self, node: TSNode, enclosing_fqn: str | None, container_id: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        if enclosing_fqn:
            fqn = f"{enclosing_fqn}.{name}"
        elif self.pkg:
            fqn = f"{self.pkg}.{name}"
        else:
            fqn = name
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
        # container (File or enclosing Class) CONTAINS this Class
        self._edge(CONTAINS, container_id, fqn)
        self.file_classes.append(fqn)
        self.class_refs.setdefault(fqn, set())

        body = node.child_by_field_name("body")
        if body is None:
            return
        self.class_bodies[fqn] = body
        for member in body.named_children:
            if member.type in _METHOD_DECLS:
                self._method_decl(member, class_fqn=fqn)
            elif member.type in _TYPE_DECLS:
                self._type_decl(member, enclosing_fqn=fqn, container_id=fqn)
            elif member.type == "field_declaration":
                ref = _simple_type_name(member.child_by_field_name("type"))
                if ref:
                    self.class_refs[fqn].add(ref)

    def _method_decl(self, node: TSNode, class_fqn: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        param_type_names = _param_types(node)
        params = ",".join(param_type_names)
        fqn = f"{class_fqn}#{name}({params})"
        # parameter types are dependencies of the declaring class
        for ref in param_type_names:
            if ref and ref != "?":
                self.class_refs.setdefault(class_fqn, set()).add(ref)
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
        self.method_calls[fqn] = _collect_call_names(node)


def _collect_call_names(method: TSNode) -> set[str]:
    """Simple method names invoked anywhere in ``method``'s subtree."""
    names: set[str] = set()
    stack = [method]
    while stack:
        n = stack.pop()
        if n.type == "method_invocation":
            nm = n.child_by_field_name("name")
            if nm is not None:
                names.add(nm.text.decode())
        stack.extend(n.children)
    return names


def _index_by_simple_name(nodes: list[Node], kind: str) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for n in nodes:
        if n.kind == kind:
            idx.setdefault(n.name, []).append(n.qualified_name)
    return idx


def _depends_on_edges(
    nodes: list[Node], class_refs: dict[str, set[str]], prov: Provenance
) -> list[Edge]:
    by_simple = _index_by_simple_name(nodes, CLASS)
    seen: set[tuple[str, str]] = set()
    out: list[Edge] = []
    for src_fqn, refs in class_refs.items():
        for ref in refs:
            for dst_fqn in by_simple.get(ref, ()):
                if dst_fqn == src_fqn or (src_fqn, dst_fqn) in seen:
                    continue
                seen.add((src_fqn, dst_fqn))
                out.append(Edge(kind=DEPENDS_ON, src=src_fqn, dst=dst_fqn, provenance=prov))
    return out


def _calls_edges(
    nodes: list[Node], method_calls: dict[str, set[str]], prov: Provenance
) -> list[Edge]:
    # Index Method nodes by simple name. CALLS is name-based best-effort: a call
    # to `foo(...)` links to every known method named `foo`. Self-loops (a name
    # equal to the caller's own) are suppressed — with no type resolution they are
    # the most likely false positive.
    by_simple = _index_by_simple_name(nodes, METHOD)
    seen: set[tuple[str, str]] = set()
    out: list[Edge] = []
    for src_fqn, names in method_calls.items():
        for name in names:
            for dst_fqn in by_simple.get(name, ()):
                if dst_fqn == src_fqn or (src_fqn, dst_fqn) in seen:
                    continue
                seen.add((src_fqn, dst_fqn))
                out.append(Edge(kind=CALLS, src=src_fqn, dst=dst_fqn, provenance=prov))
    return out


def _iter_java_files(root: Path):
    for p in sorted(root.rglob("*.java")):
        if p.is_file():
            yield p


def extract(root: Path | str, provenance: Provenance, repo_name: str | None = None) -> IR:
    """Parse every ``*.java`` file under ``root`` into an :class:`IR`.

    ``root`` is treated as the repository root; File node paths are recorded
    repo-relative to it. Every node and edge carries ``provenance``.
    """
    root = Path(root)
    repo_name = repo_name or root.name
    parser = _parser()

    nodes: list[Node] = []
    edges: list[Edge] = []
    packages: set[str] = set()
    class_refs: dict[str, set[str]] = {}
    method_calls: dict[str, set[str]] = {}

    for path in _iter_java_files(root):
        source = path.read_bytes()
        tree = parser.parse(source)
        rel = path.relative_to(root).as_posix()
        walker = _FileWalker(rel, source, tree.root_node, provenance)
        walker.run()
        nodes.extend(walker.nodes)
        edges.extend(walker.edges)
        if walker.pkg:
            packages.add(walker.pkg)
        for fqn, refs in walker.class_refs.items():
            class_refs.setdefault(fqn, set()).update(refs)
        method_calls.update(walker.method_calls)

    # DEPENDS_ON (Class->Class): resolve referenced simple type names against
    # known classes by unqualified name (best-effort, no full type resolution).
    edges.extend(_depends_on_edges(nodes, class_refs, provenance))
    # CALLS (Method->Method): name-based best-effort resolution.
    edges.extend(_calls_edges(nodes, method_calls, provenance))

    # Repo node + Package nodes + Repo->Package CONTAINS
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
