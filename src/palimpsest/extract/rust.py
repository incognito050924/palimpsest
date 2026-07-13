"""Static extraction of Rust source into the palimpsest IR.

Parser: tree-sitter-rust (py-tree-sitter). Deterministic structural ontology only,
mirroring the Kotlin/Python extractors (ADR-20260706 §결정6: per-language
``queries/<lang>/*.scm`` own the build-less tree-sitter spine, the resolver stays
language-neutral).

Rust has NO class. A free ``fn``, an ``impl`` method, and a ``trait`` method all
share ONE grammar node (``function_item``); they are told apart ONLY by parent
context, done structurally in the walker:

  - ``function_item`` directly under a module (source_file / ``mod`` body) -> Function
    (File CONTAINS)
  - ``function_item`` (or a signature-only ``function_signature_item``) under an
    ``impl_item`` / ``trait_item`` body -> Method (Class CONTAINS), where the Class is
    the implemented / declaring type
  - ``struct`` / ``enum`` / ``trait`` -> Class

The container of a top-level function is always the FILE (module de-Class'd, per the
`_unit_of` generalization in kg/community.py), never a ``mod`` node — the inline
``mod`` path lives only in the identity string.

Identity (``qualified_name``), module-path scheme (A-Q6), ``::``-separated:
  - Function : mod::path::name(paramTypes)          (no declaring type)
  - Class    : mod::path::Name
  - Method   : mod::path::Type#name(paramTypes)

Parameters use simple type names (Rust is statically typed, mirroring Kotlin/Java);
the ``self`` receiver is not part of the identity. CALLS is resolved name-based for
this first slice — receiver typing is out of scope. Generic monomorphization,
macro-expanded items, and DEPENDS_ON (the resolver path) are out of scope.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import tree_sitter_rust as tsrust
from tree_sitter import Language, Parser, Query, QueryCursor, Node as TSNode

from palimpsest.ir import IR, Node, Edge, Provenance
from palimpsest.ir import REPO, FILE, CLASS, METHOD, FUNCTION
from palimpsest.ir import CONTAINS, CALLS, IMPORTS

_LANGUAGE = Language(tsrust.language())

# Per-language tree-sitter query (ADR-20260706 §결정6): `tags.scm` yields the call
# references the name-based resolver consumes. A new language plugs in by adding
# its own queries/<lang>/*.scm; the resolver below stays language-agnostic.
_QUERY_DIR = Path(__file__).parent / "queries" / "rust"
_TAGS_QUERY = Query(_LANGUAGE, (_QUERY_DIR / "tags.scm").read_text())

_TYPE_ITEMS = frozenset({"struct_item", "enum_item", "trait_item", "union_item"})


def _parser() -> Parser:
    return Parser(_LANGUAGE)


def _line(point) -> int:
    return point[0] + 1


def _type_simple(node: TSNode | None) -> str:
    """Base (unqualified, un-generic, de-referenced) type name, or ``?`` when absent.

    The first ``type_identifier``/``primitive_type`` in the subtree — this drops
    ``&``/``mut``/lifetimes (reference_type wrappers) and generic arguments (the
    outer ``type_identifier`` of a ``generic_type`` is visited first).
    """
    if node is None:
        return "?"
    if node.type in ("type_identifier", "primitive_type"):
        return node.text.decode()
    for c in node.named_children:
        r = _type_simple(c)
        if r != "?":
            return r
    return "?"


def _param_types(func: TSNode) -> list[str]:
    """Simple type names of ``func``'s value parameters (positional, for identity).

    The ``self`` receiver (``self_parameter``) is NOT part of the identity — it is
    skipped, mirroring the Kotlin extractor dropping the receiver.
    """
    params = func.child_by_field_name("parameters")
    if params is None:
        return []
    out: list[str] = []
    for p in params.named_children:
        if p.type == "parameter":
            out.append(_type_simple(p.child_by_field_name("type")))
    return out


def _use_target(arg: TSNode) -> str | None:
    """The module path a ``use`` clause references (the IMPORTS edge destination).

    A braced list ``a::b::{c, d}`` or a wildcard ``a::b::*`` resolves to its common
    prefix ``a::b``; an aliased ``a::b as c`` resolves to the path ``a::b``.
    """
    t = arg.type
    if t in ("identifier", "scoped_identifier", "crate", "super", "self"):
        return arg.text.decode()
    if t in ("scoped_use_list", "use_wildcard", "use_as_clause"):
        path = arg.child_by_field_name("path")
        if path is not None:
            return path.text.decode()
        # a bare `{...}` / `*` at the crate root has no prefix path
        return None
    return None


class _FileWalker:
    """Collects structural nodes + CONTAINS/IMPORTS edges for one parsed Rust file."""

    def __init__(self, rel_path: str, source: bytes, root: TSNode, prov: Provenance):
        self.rel_path = rel_path
        self.source = source
        self.root = root
        self.prov = prov
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []

    def _edge(self, kind: str, src: str, dst: str) -> None:
        self.edges.append(Edge(kind=kind, src=src, dst=dst, provenance=self.prov))

    def _mod_prefix(self, mod_path: list[str]) -> str:
        return "::".join(mod_path) + "::" if mod_path else ""

    def _callable_fqn(self, name: str, params: list[str], class_fqn: str | None,
                      mod_path: list[str]) -> str:
        joined = ",".join(params)
        if class_fqn is not None:
            return f"{class_fqn}#{name}({joined})"
        return f"{self._mod_prefix(mod_path)}{name}({joined})"

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
        self._walk(self.root, mod_path=[])

    def _walk(self, container: TSNode, mod_path: list[str]) -> None:
        """Dispatch module-direct items; recurse into inline ``mod`` bodies.

        ``container`` is a ``source_file`` (root) or a ``mod`` ``declaration_list`` —
        both expose their items as named children.
        """
        for child in container.named_children:
            t = child.type
            if t == "function_item":
                self._function_item(child, mod_path, class_fqn=None)
            elif t in _TYPE_ITEMS:
                self._type_item(child, mod_path)
            elif t == "impl_item":
                self._impl_item(child, mod_path)
            elif t == "mod_item":
                body = child.child_by_field_name("body")
                name_node = child.child_by_field_name("name")
                if body is not None and name_node is not None:
                    self._walk(body, mod_path + [name_node.text.decode()])
            elif t == "use_declaration":
                self._use(child)

    def _function_item(self, node: TSNode, mod_path: list[str],
                       class_fqn: str | None) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        fqn = self._callable_fqn(name, _param_types(node), class_fqn, mod_path)
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
        # A top-level function is contained by the FILE (module de-Class'd); a method
        # by its declaring/implemented type.
        self._edge(CONTAINS, class_fqn if class_fqn is not None else self.rel_path, fqn)

    def _type_item(self, node: TSNode, mod_path: list[str]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        fqn = f"{self._mod_prefix(mod_path)}{name}"
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
        # A trait declares methods (default-bodied `function_item` and signature-only
        # `function_signature_item`); attach them to the trait as Methods.
        if node.type == "trait_item":
            self._methods_of(node, class_fqn=fqn, mod_path=mod_path)

    def _impl_item(self, node: TSNode, mod_path: list[str]) -> None:
        # An impl block's methods attach to the IMPLEMENTED type (the `type` field),
        # regardless of whether it is a trait impl (`impl Trait for Type`).
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return
        class_fqn = f"{self._mod_prefix(mod_path)}{_type_simple(type_node)}"
        self._methods_of(node, class_fqn=class_fqn, mod_path=mod_path)

    def _methods_of(self, node: TSNode, class_fqn: str, mod_path: list[str]) -> None:
        body = node.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:
            if member.type == "function_item":
                self._function_item(member, mod_path, class_fqn=class_fqn)
            elif member.type == "function_signature_item":
                self._signature_item(member, class_fqn)

    def _signature_item(self, node: TSNode, class_fqn: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        fqn = f"{class_fqn}#{name}({','.join(_param_types(node))})"
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

    def _use(self, node: TSNode) -> None:
        # File IMPORTS the referenced module. External/unresolved paths get NO node
        # (honest external — the edge dangles, no phantom is fabricated).
        arg = node.child_by_field_name("argument")
        if arg is None:
            return
        target = _use_target(arg)
        if target:
            self._edge(IMPORTS, self.rel_path, target)


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
    simple name. Self-loops are suppressed; edges dedup by (src, dst).
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


def _iter_rust_files(root: Path):
    for p in sorted(root.rglob("*.rs")):
        if p.is_file() and "target" not in p.relative_to(root).parts:
            yield p


def extract(root: Path | str, provenance: Provenance, repo_name: str | None = None) -> IR:
    """Parse every ``*.rs`` file under ``root`` into an :class:`IR`.

    ``root`` is treated as the repository root; File node paths are recorded
    repo-relative to it. The Cargo ``target/`` build output is pruned. Every node and
    edge carries ``provenance``.
    """
    root = Path(root)
    repo_name = repo_name or root.name
    parser = _parser()

    nodes: list[Node] = []
    edges: list[Edge] = []
    call_sites: list[CallSite] = []

    for path in _iter_rust_files(root):
        source = path.read_bytes()
        tree = parser.parse(source)
        rel = path.relative_to(root).as_posix()
        walker = _FileWalker(rel, source, tree.root_node, provenance)
        walker.run()
        nodes.extend(walker.nodes)
        edges.extend(walker.edges)
        call_sites.extend(_scan_calls(rel, tree.root_node))

    edges.extend(_calls_edges(nodes, call_sites, provenance))

    repo = Node(kind=REPO, qualified_name=repo_name, name=repo_name, provenance=provenance)
    return IR(nodes=[repo] + nodes, edges=edges)
