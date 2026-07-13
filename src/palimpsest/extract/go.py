"""Static extraction of Go source into the palimpsest IR.

Parser: tree-sitter-go (py-tree-sitter). Deterministic structural ontology only,
mirroring the Kotlin extractor (ADR-20260706 §결정6: per-language
``queries/<lang>/*.scm`` own the build-less tree-sitter spine, the resolver stays
language-neutral).

Go has NO classes — the de-Class ontology (commit 138db70) is its natural home:

  - A top-level ``function_declaration`` (no receiver) is a **Function**, contained
    by its **File** (the module container).
  - A ``method_declaration`` (has a receiver) is a **Method**, contained by its
    receiver *type* — a **Class** node emitted from the ``type X struct|interface|…``
    declaration. Every receiver type is defined in the same package (Go rule), so a
    Method's Class container never dangles even across files.
  - Every top-level ``type_spec`` (struct / interface / named type) is a **Class**:
    it is a Method container, a Community grouping unit, and a DEPENDS_ON target
    (mirrors Java, where interface/enum/record all become Class).
  - Interface *method signatures* are NOT emitted as nodes — they have no body and
    no call sites, so they add nothing to the call graph or grouping (§4-3).

Identity (``qualified_name``). Go's package identity is its directory (the import
path minus the module prefix), so — resolving design-note [A] Q6 — the Package is the
**repo-relative directory** and callable identity prefixes it:

  - Package  : ``<dir>``                              (e.g. ``internal/config``)
  - Class    : ``<dir>.Type``
  - Function : ``<dir>.name(paramTypes)``             (no declaring class)
  - Method   : ``<dir>.RecvType#name(paramTypes)``

Go has no overloading, so ``paramTypes`` are cosmetic (identity is already unique)
but kept for one shared callable-identity scheme across languages.

CALLS is name-based for this extractor (like Kotlin/Python), resolved only within
the Go node set — the SC-B language-local boundary (ADR-20260713 결정2) holds
trivially for a single-language extract. DEPENDS_ON is emitted from static type
references in struct fields and function/method parameter+result types, resolved
name-based against Class nodes (ADR-20260713 결정1: Go is statically typed).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import tree_sitter_go as tsgo
from tree_sitter import Language, Parser, Query, QueryCursor, Node as TSNode

from palimpsest.ir import IR, Node, Edge, Provenance
from palimpsest.ir import REPO, PACKAGE, FILE, CLASS, METHOD, FUNCTION
from palimpsest.ir import CONTAINS, CALLS, DEPENDS_ON, IMPORTS

_LANGUAGE = Language(tsgo.language())

# Per-language tree-sitter query (ADR-20260706 §결정6): `tags.scm` yields the call
# references the name-based resolver consumes. A new language plugs in by adding
# its own queries/<lang>/*.scm; the resolver below stays language-agnostic.
_QUERY_DIR = Path(__file__).parent / "queries" / "go"
_TAGS_QUERY = Query(_LANGUAGE, (_QUERY_DIR / "tags.scm").read_text())

# Go vendored third-party code lives under a `vendor/` directory — never the
# project's own graph (mirrors the ecmascript vendored-exclusion boundary).
_EXCLUDED_DIRS = frozenset({"vendor"})


def _parser() -> Parser:
    return Parser(_LANGUAGE)


def _line(point) -> int:
    return point[0] + 1


def _package_name(root: TSNode) -> str:
    """The `package <name>` clause identifier (the Go package's local name)."""
    for c in root.named_children:
        if c.type == "package_clause":
            ident = c.child_by_field_name("name") or _first(c, "package_identifier")
            if ident is not None:
                return ident.text.decode()
    return ""


def _first(node: TSNode, type_name: str) -> TSNode | None:
    for c in node.named_children:
        if c.type == type_name:
            return c
    return None


def _type_identifiers(node: TSNode | None) -> list[str]:
    """Every ``type_identifier`` leaf name referenced inside a type expression.

    ``*Spec`` → [Spec]; ``[]Entry`` → [Entry]; ``map[string]Foo`` → [string, Foo];
    ``domain.Doc`` (qualified_type) → [Doc]. Used for DEPENDS_ON (all referenced
    named types) and, via :func:`_type_base_name`, for identity.
    """
    if node is None:
        return []
    out: list[str] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "type_identifier":
            out.append(n.text.decode())
            continue
        stack.extend(reversed(n.named_children))
    return out


def _type_base_name(node: TSNode | None) -> str:
    """The DEFINED base type name of a type expression, or ``?`` when absent.

    Strips wrappers so identity keys on the type itself, never a type *argument*:
    ``*Spec``→Spec, ``[]Entry``→Entry, ``domain.Doc``→Doc, ``Stack[T]``→Stack,
    ``*Stack[T]``→Stack. Getting the generic case wrong would key a method on its
    type parameter (``pkg.T#m``) — dangling its Class and colliding distinct generic
    types onto one node — so ``generic_type`` recurses into its base, not its args.
    """
    if node is None:
        return "?"
    t = node.type
    if t == "type_identifier":
        return node.text.decode()
    if t == "generic_type":
        return _type_base_name(node.child_by_field_name("type"))
    if t == "pointer_type":
        inner = node.named_children[0] if node.named_children else None
        return _type_base_name(inner)
    if t in ("slice_type", "array_type"):
        return _type_base_name(node.child_by_field_name("element"))
    if t == "map_type":
        return _type_base_name(node.child_by_field_name("value"))
    if t == "channel_type":
        return _type_base_name(node.child_by_field_name("value"))
    # qualified_type (`pkg.Type`) and any other shape: the type's own name is the
    # last-declared type_identifier (the leaf after the package qualifier).
    names = _type_identifiers(node)
    return names[-1] if names else "?"


def _param_types(param_list: TSNode | None) -> list[str]:
    """Base type names of a ``parameters`` list (positional, for identity)."""
    if param_list is None:
        return []
    out: list[str] = []
    for p in param_list.named_children:
        if p.type != "parameter_declaration":
            continue
        out.append(_type_base_name(p.child_by_field_name("type")))
    return out


def _receiver_type(method: TSNode) -> str | None:
    """The receiver's base type name for a ``method_declaration`` (strips ``*``)."""
    recv = method.child_by_field_name("receiver")
    if recv is None:
        return None
    decl = _first(recv, "parameter_declaration")
    if decl is None:
        return None
    return _type_base_name(decl.child_by_field_name("type"))


def _signature_refs(node: TSNode) -> list[str]:
    """Referenced type names in a func/method signature (parameters + result).

    Excludes the receiver (a separate ``receiver`` field), which is the container,
    not a dependency. Feeds DEPENDS_ON (ADR-20260713 결정1).
    """
    refs: list[str] = []
    for field in ("parameters", "result"):
        refs.extend(_type_identifiers(node.child_by_field_name(field)))
    return refs


def _import_specs(decl: TSNode):
    """The ``import_spec`` nodes of an ``import_declaration`` — single or grouped."""
    for c in decl.named_children:
        if c.type == "import_spec":
            yield c
        elif c.type == "import_spec_list":
            for cc in c.named_children:
                if cc.type == "import_spec":
                    yield cc


class _FileWalker:
    """Structural nodes + CONTAINS/IMPORTS + DEPENDS_ON refs for one parsed Go file."""

    def __init__(self, rel_path: str, pkg: str, source: bytes, root: TSNode, prov: Provenance):
        self.rel_path = rel_path
        self.pkg = pkg  # repo-relative directory = Go package identity
        self.source = source
        self.root = root
        self.prov = prov
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        # container fqn -> referenced simple type names (DEPENDS_ON, resolved later)
        self.type_refs: dict[str, set[str]] = defaultdict(set)
        self.imports_test = False  # set when a `testing` import is seen

    def _edge(self, kind: str, src: str, dst: str) -> None:
        self.edges.append(Edge(kind=kind, src=src, dst=dst, provenance=self.prov))

    def _prefix(self) -> str:
        return f"{self.pkg}." if self.pkg else ""

    def _func_fqn(self, name: str, params: list[str]) -> str:
        return f"{self._prefix()}{name}({','.join(params)})"

    def _class_fqn(self, name: str) -> str:
        return f"{self._prefix()}{name}"

    def _method_fqn(self, recv_type: str, name: str, params: list[str]) -> str:
        return f"{self._prefix()}{recv_type}#{name}({','.join(params)})"

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
        for child in self.root.named_children:
            if child.type == "function_declaration":
                self._function_decl(child)
            elif child.type == "method_declaration":
                self._method_decl(child)
            elif child.type == "type_declaration":
                self._type_decl(child)
            elif child.type == "import_declaration":
                self._import_decl(child)
        # is_test marker (issue #17): *_test.go filename or a `testing` import marks
        # every code-unit node this file produced. Pure PROPERTY, post-walk (java.py).
        if self.rel_path.endswith("_test.go") or self.imports_test:
            for n in self.nodes:
                if n.kind in (FILE, CLASS, METHOD, FUNCTION):
                    n.is_test = True

    def _import_decl(self, node: TSNode) -> None:
        for spec in _import_specs(node):
            path_node = spec.child_by_field_name("path")
            if path_node is None:
                continue
            target = path_node.text.decode().strip('"').strip("`")
            if target:
                self._edge(IMPORTS, self.rel_path, target)
                if target == "testing" or target.startswith("testing/"):
                    self.imports_test = True

    def _type_decl(self, node: TSNode) -> None:
        # `type ( A struct{}; B int )` groups several type_specs under one decl.
        for spec in node.named_children:
            if spec.type != "type_spec":
                continue
            name_node = spec.child_by_field_name("name")
            if name_node is None:
                continue
            name = name_node.text.decode()
            fqn = self._class_fqn(name)
            self.nodes.append(
                Node(
                    kind=CLASS,
                    qualified_name=fqn,
                    name=name,
                    provenance=self.prov,
                    path=self.rel_path,
                    start_line=_line(spec.start_point),
                    end_line=_line(spec.end_point),
                )
            )
            self._edge(CONTAINS, self.rel_path, fqn)
            # struct field types are this type's dependencies (F3): Class -> Class.
            type_node = spec.child_by_field_name("type")
            if type_node is not None and type_node.type == "struct_type":
                self.type_refs[fqn].update(_type_identifiers(type_node))

    def _method_decl(self, node: TSNode) -> None:
        recv_type = _receiver_type(node)
        name_node = node.child_by_field_name("name")
        if recv_type is None or recv_type == "?" or name_node is None:
            return
        name = name_node.text.decode()
        params = _param_types(node.child_by_field_name("parameters"))
        class_fqn = self._class_fqn(recv_type)
        fqn = self._method_fqn(recv_type, name, params)
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
        # method parameter/result types are dependencies of the receiver Class.
        self.type_refs[class_fqn].update(_signature_refs(node))

    def _function_decl(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        params = _param_types(node.child_by_field_name("parameters"))
        fqn = self._func_fqn(name, params)
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
        # top-level func parameter/result types are dependencies of its File/Module
        # (the de-Class generalization — no declaring class to hang them on).
        self.type_refs[self.rel_path].update(_signature_refs(node))


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
    """CALLS (Function|Method -> Function|Method) resolved name-based for Go.

    The enclosing callable (source) is the innermost Function/Method line-range
    containing the call site; the target is every callable sharing the callee's
    simple name. Self-loops are suppressed. Resolution stays within the Go node set
    (SC-B language-local, trivially so for a single-language extract).
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


def _depends_on_edges(
    nodes: list[Node], type_refs: dict[str, set[str]], prov: Provenance
) -> list[Edge]:
    """DEPENDS_ON resolved name-based against Class nodes (Go named types).

    ``src`` may be a Class fqn (struct field / method param ref) OR a File/Module fqn
    (top-level-function param ref) — the de-Class generalization. A ref that matches
    no Class node (a builtin like ``string`` or an external ``domain.Doc``) is
    dropped, never invented (ADR-20260713 결정1). Self-loops and dups suppressed.
    """
    by_simple: dict[str, list[str]] = defaultdict(list)
    for n in nodes:
        if n.kind == CLASS:
            by_simple[n.name].append(n.qualified_name)

    seen: set[tuple[str, str]] = set()
    out: list[Edge] = []
    for src_fqn, refs in type_refs.items():
        for ref in refs:
            for dst in by_simple.get(ref, ()):
                if dst == src_fqn or (src_fqn, dst) in seen:
                    continue
                seen.add((src_fqn, dst))
                out.append(Edge(kind=DEPENDS_ON, src=src_fqn, dst=dst, provenance=prov))
    return out


def _iter_go_files(root: Path):
    for p in sorted(root.rglob("*.go")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if _EXCLUDED_DIRS.intersection(rel_parts[:-1]):
            continue
        yield p


def extract(root: Path | str, provenance: Provenance, repo_name: str | None = None) -> IR:
    """Parse every ``*.go`` file under ``root`` (excluding ``vendor/``) into an IR.

    ``root`` is treated as the repository root; File node paths are recorded
    repo-relative to it. Every node and edge carries ``provenance``.
    """
    root = Path(root)
    repo_name = repo_name or root.name
    parser = _parser()

    nodes: list[Node] = []
    edges: list[Edge] = []
    packages: dict[str, str] = {}  # dir -> package clause name
    call_sites: list[CallSite] = []
    type_refs: dict[str, set[str]] = defaultdict(set)

    for path in _iter_go_files(root):
        source = path.read_bytes()
        tree = parser.parse(source)
        rel = path.relative_to(root).as_posix()
        pkg_dir = str(Path(rel).parent.as_posix())
        if pkg_dir == ".":
            pkg_dir = ""
        walker = _FileWalker(rel, pkg_dir, source, tree.root_node, provenance)
        walker.run()
        nodes.extend(walker.nodes)
        edges.extend(walker.edges)
        if pkg_dir:
            packages.setdefault(pkg_dir, _package_name(tree.root_node))
        call_sites.extend(_scan_calls(rel, tree.root_node))
        for container, refs in walker.type_refs.items():
            type_refs[container].update(refs)

    edges.extend(_calls_edges(nodes, call_sites, provenance))
    edges.extend(_depends_on_edges(nodes, type_refs, provenance))

    repo = Node(kind=REPO, qualified_name=repo_name, name=repo_name, provenance=provenance)
    repo_and_pkgs: list[Node] = [repo]
    repo_edges: list[Edge] = []
    for pkg_dir in sorted(packages):
        repo_and_pkgs.append(
            Node(
                kind=PACKAGE,
                qualified_name=pkg_dir,
                name=packages[pkg_dir] or pkg_dir.rsplit("/", 1)[-1],
                provenance=provenance,
            )
        )
        repo_edges.append(Edge(kind=CONTAINS, src=repo_name, dst=pkg_dir, provenance=provenance))

    return IR(nodes=repo_and_pkgs + nodes, edges=repo_edges + edges)
