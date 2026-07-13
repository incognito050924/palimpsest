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
from palimpsest.ir import REPO, PACKAGE, FILE, CLASS, METHOD, FUNCTION, ENDPOINT
from palimpsest.ir import CONTAINS, CALLS, DEPENDS_ON, REALIZES, HANDLES
from palimpsest.extract.spring import AnnotationInfo, spring_role, spring_endpoints

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


# --- Kotlin annotation reader (the grammar-specific feeder for extract.spring) --------
# java.py reads its own tree into shared `AnnotationInfo` records; this is the Kotlin
# structural parallel over the tree-sitter-kotlin grammar, which shapes annotations
# differently: a bare `@RestController` is an `annotation` wrapping a `user_type`, an
# `@GetMapping("/x")` is an `annotation` wrapping a `constructor_invocation`
# (`user_type` + `value_arguments`), and array/named args (`value = ["/x"]`,
# `method = [RequestMethod.POST]`) read through `collection_literal` nodes. Normalizing
# all of these here lets the shared mapper see identical inputs on both tiers.

def _ann_simple_name(user_type: TSNode | None) -> str:
    """Simple annotation name off a ``user_type`` node (``@a.b.Get`` -> ``Get``)."""
    if user_type is None:
        return ""
    return user_type.text.decode().split("<")[0].split(".")[-1].strip()


def _string_value(node: TSNode) -> str | None:
    """Inner text of a Kotlin ``string_literal`` (surrounding quotes stripped), else None."""
    if node.type != "string_literal":
        return None
    text = node.text.decode()
    return text[1:-1] if len(text) >= 2 else ""


def _string_literals(node: TSNode) -> list[str]:
    """String-literal values of an annotation argument — a bare literal, or every
    literal inside a ``[...]`` ``collection_literal`` (``@GetMapping(["/a","/b"])``).
    Non-string elements are dropped."""
    lit = _string_value(node)
    if lit is not None:
        return [lit]
    if node.type == "collection_literal":
        out: list[str] = []
        for child in node.named_children:
            out.extend(_string_literals(child))
        return out
    return []


def _annotation_info(ann: TSNode) -> AnnotationInfo | None:
    """Read one Kotlin ``annotation`` node into an :class:`AnnotationInfo`.

    Positional string-literal args become ``args``; named ``key = value`` attributes
    become ``named_args`` (string literals — bare or single-element arrays — unquoted;
    other value expressions, e.g. ``[RequestMethod.POST]``, kept verbatim so the shared
    mapper's ``RequestMethod.X`` regex still finds the verb)."""
    if ann.type != "annotation":
        return None
    inner = None
    for c in ann.named_children:
        if c.type in ("user_type", "constructor_invocation"):
            inner = c
            break
    if inner is None:
        return None
    if inner.type == "user_type":
        return AnnotationInfo(name=_ann_simple_name(inner))

    # constructor_invocation: `Name(arg, key = value, ...)`
    user_type = None
    value_args = None
    for c in inner.named_children:
        if c.type == "user_type":
            user_type = c
        elif c.type == "value_arguments":
            value_args = c
    args: list[str] = []
    named: dict[str, str] = {}
    if value_args is not None:
        for va in value_args.named_children:
            if va.type != "value_argument":
                continue
            if any(ch.type == "=" for ch in va.children):  # named `key = value`
                key = None
                val_node = None
                for ch in va.named_children:
                    if key is None and ch.type == "identifier":
                        key = ch.text.decode()
                    else:
                        val_node = ch
                if key is not None and val_node is not None:
                    lits = _string_literals(val_node)
                    named[key] = lits[0] if lits else val_node.text.decode()
            else:  # positional
                for ch in va.named_children:
                    args.extend(_string_literals(ch))
    return AnnotationInfo(name=_ann_simple_name(user_type), args=tuple(args), named_args=named)


def _annotations(decl_node: TSNode) -> list[AnnotationInfo]:
    """Every annotation on a class/function declaration's ``modifiers`` — the Kotlin
    reader that feeds the shared ``spring`` mapper (java.py's ``_annotations`` parallel)."""
    out: list[AnnotationInfo] = []
    for child in decl_node.named_children:
        if child.type != "modifiers":
            continue
        for ann in child.named_children:
            info = _annotation_info(ann)
            if info is not None:
                out.append(info)
    return out


def _type_after_name(node: TSNode) -> TSNode | None:
    """The type node that follows the declared name in a ``class_parameter`` /
    ``variable_declaration``: skip a leading ``modifiers`` block and the first
    ``identifier`` (the parameter/property name); the next named child is the type."""
    seen_name = False
    for c in node.named_children:
        if c.type == "modifiers":
            continue
        if not seen_name and c.type == "identifier":
            seen_name = True
            continue
        return c
    return None


def _ctor_param_type_names(class_node: TSNode) -> list[str]:
    """Simple type names of the class's PRIMARY-CONSTRUCTOR value params — Kotlin's
    constructor-injection surface (so injected deps reach DEPENDS_ON, ac-9)."""
    out: list[str] = []
    for c in class_node.named_children:
        if c.type != "primary_constructor":
            continue
        for cps in c.named_children:
            if cps.type != "class_parameters":
                continue
            for cp in cps.named_children:
                if cp.type == "class_parameter":
                    out.append(_type_simple(_type_after_name(cp)))
    return out


def _property_type_name(prop_decl: TSNode) -> str:
    """Simple type name of a property (field), or ``?`` when the type is inferred —
    covers ``@Autowired`` field injection."""
    for c in prop_decl.named_children:
        if c.type == "variable_declaration":
            return _type_simple(_type_after_name(c))
    return "?"


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
        # class fqn -> referenced simple type names (ctor-injection params + fields),
        # resolved to Class->Class DEPENDS_ON at corpus assembly (ac-9).
        self.class_refs: dict[str, set[str]] = {}

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
        class_anns = _annotations(node)
        self.nodes.append(
            Node(
                kind=CLASS,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=_line(node.start_point),
                end_line=_line(node.end_point),
                # Spring DI/stereotype role marker (design contract Decision 6): a pure
                # property off identity; None when the class carries no stereotype.
                role=spring_role(class_anns),
            )
        )
        self._edge(CONTAINS, self.rel_path, fqn)
        refs = self.class_refs.setdefault(fqn, set())
        # Constructor-injection params are dependencies of the declaring class (injected
        # deps reach DEPENDS_ON — no new edge kind, ac-9).
        for ref in _ctor_param_type_names(node):
            if ref and ref != "?":
                refs.add(ref)
        body = None
        for c in node.named_children:
            if c.type == "class_body":
                body = c
                break
        if body is None:
            return
        for member in body.named_children:
            if member.type == "function_declaration":
                self._method_decl(member, class_fqn=fqn, class_anns=class_anns)
            elif member.type == "property_declaration":
                # property/field types (incl. @Autowired field injection) are deps too.
                ref = _property_type_name(member)
                if ref and ref != "?":
                    refs.add(ref)

    def _method_decl(
        self, node: TSNode, class_fqn: str, class_anns: list[AnnotationInfo]
    ) -> None:
        name = _name_of(node)
        if name is None:
            return
        fqn = self._callable_fqn(name, _param_types(node), class_fqn=class_fqn)
        method_anns = _annotations(node)
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
        # Spring HTTP-API Endpoints (design contract Decisions 1 & 2), mirroring java.py:
        # a controller handler realizes one or more ``spring:``-discriminated Endpoints;
        # the File REALIZES each and the handler Method HANDLES it. A non-controller /
        # view-returning method yields [] from the shared mapper.
        for method_token, ep_qn in spring_endpoints(class_anns, method_anns):
            self.nodes.append(
                Node(
                    kind=ENDPOINT,
                    qualified_name=ep_qn,
                    name=method_token,
                    provenance=self.prov,
                    path=self.rel_path,
                    start_line=_line(node.start_point),
                    end_line=_line(node.end_point),
                )
            )
            self._edge(REALIZES, self.rel_path, ep_qn)
            self._edge(HANDLES, fqn, ep_qn)


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


def _index_by_simple_name(nodes: list[Node], kind: str) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for n in nodes:
        if n.kind == kind:
            idx.setdefault(n.name, []).append(n.qualified_name)
    return idx


def _depends_on_edges(
    nodes: list[Node], class_refs: dict[str, set[str]], prov: Provenance
) -> list[Edge]:
    """DEPENDS_ON (Class->Class): resolve referenced simple type names (ctor-injection
    params + property fields) against known classes by unqualified name — best-effort,
    no full type resolution (java.py parallel). Self-loops and dupes suppressed."""
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
    class_refs: dict[str, set[str]] = {}
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
        for fqn, refs in walker.class_refs.items():
            class_refs.setdefault(fqn, set()).update(refs)
        call_sites.extend(_scan_calls(rel, tree.root_node))

    # DEPENDS_ON (Class->Class): resolve referenced simple type names against known
    # classes by unqualified name (injected deps reuse this edge — no new kind, ac-9).
    edges.extend(_depends_on_edges(nodes, class_refs, provenance))
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
