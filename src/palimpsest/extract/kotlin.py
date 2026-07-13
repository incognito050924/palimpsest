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
from palimpsest.extract.jvm_http import (
    JvmHttpCall,
    UriHelper,
    DataflowRecovery,
    api_call_nodes,
    dataflow_api_call_nodes,
    call_verb,
    http_method_of_arg,
)
from palimpsest.extract.http_origins import is_recognized_call

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
        # single-type import SIMPLE name -> resolved Java FQN (the HTTP scanner resolves
        # a receiver's declared type through this to a registered construct origin).
        self.import_fqn: dict[str, str] = {}

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
            elif child.type == "import":
                self._import(child)
        # is_test marker (issue #17): Kotlin parses no imports/annotations here, so
        # the signal is the Gradle `src/test` path convention. Marks every code-unit
        # node this file produced — pure PROPERTY, post-walk (mirrors java.py).
        if _is_test_path(self.rel_path):
            for n in self.nodes:
                if n.kind in (FILE, CLASS, METHOD, FUNCTION):
                    n.is_test = True
        # JVM outbound-HTTP caller scan (wi_260713iah): a recognized literal-URL call
        # (receiver resolves via import to a registered construct) -> ApiCall node.
        # Shares the grammar-agnostic recognizer with java.py (no per-tier divergence).
        var_types = _kotlin_var_types(self.root)
        self.nodes.extend(
            api_call_nodes(
                _kotlin_http_calls(self.root, var_types),
                self.import_fqn,
                self.rel_path,
                self.prov,
            )
        )
        # One-hop param->uri dataflow (wi_260713iah part 1): mirrors java.py over the
        # Kotlin grammar — a helper whose parameter flows into a recognized uri()/verb
        # call, called with a LITERAL, recovers the ApiCall the literal scan left as gap.
        helpers = _kotlin_uri_helpers(self.root, var_types, self.import_fqn)
        if helpers:
            self.nodes.extend(
                dataflow_api_call_nodes(
                    _kotlin_helper_recoveries(self.root, helpers), self.rel_path, self.prov
                )
            )

    def _import(self, node: TSNode) -> None:
        """Bind a single-type ``import a.b.C`` — simple name ``C`` -> FQN ``a.b.C`` (a
        ``*`` wildcard import carries no single type name and is skipped)."""
        for c in node.named_children:
            if c.type == "qualified_identifier":
                fqn = c.text.decode()
                self.import_fqn[fqn.split(".")[-1]] = fqn

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


# --- JVM outbound-HTTP caller scan (wi_260713iah) ----------------------------------
# The Kotlin grammar half of the shared ``jvm_http`` recognizer, mirroring java.py:
# walk the tree into normalized ``JvmHttpCall`` records the grammar-agnostic recognizer
# scores. Kotlin shapes a call as ``call_expression(navigation_expression(recv, name),
# value_arguments)`` (vs Java's ``method_invocation``), so the traversal differs but
# the emitted records — and thus recognition — are identical across the two tiers.

def _decl_name(node: TSNode) -> str | None:
    """First ``identifier`` after an optional ``modifiers`` block — a decl's own name."""
    for c in node.named_children:
        if c.type == "modifiers":
            continue
        if c.type == "identifier":
            return c.text.decode()
    return None


def _kotlin_var_types(root: TSNode) -> dict[str, str]:
    """Map each variable NAME (ctor/function param, property) to its declared simple
    type name — the file-level symbol table a receiver identifier is resolved against.
    An inferred (type-less) declaration is skipped (``?``)."""
    out: dict[str, str] = {}
    stack = [root]
    while stack:
        n = stack.pop()
        target: TSNode | None = None
        if n.type in ("class_parameter", "parameter"):
            target = n
        elif n.type == "property_declaration":
            target = next(
                (c for c in n.named_children if c.type == "variable_declaration"), None
            )
        if target is not None:
            name = _decl_name(target)
            t = _type_simple(_type_after_name(target))
            if name and t != "?":
                out[name] = t
        stack.extend(n.named_children)
    return out


def _kotlin_http_call(call: TSNode, var_types: dict[str, str]) -> JvmHttpCall | None:
    """Read one ``call_expression`` into a :class:`JvmHttpCall` (None if it is an
    unqualified call — no navigation receiver to type)."""
    nav = None
    args = None
    for c in call.named_children:
        if c.type == "navigation_expression":
            nav = c
        elif c.type == "value_arguments":
            args = c
    if nav is None or len(nav.named_children) < 2:
        return None
    recv = nav.named_children[0]
    call_name = nav.named_children[-1].text.decode()
    # Walk the receiver chain to its root identifier, collecting the chain's verbs.
    chain_verbs: list[str] = []
    root = recv
    while root is not None and root.type == "call_expression":
        inner_nav = next(
            (c for c in root.named_children if c.type == "navigation_expression"), None
        )
        if inner_nav is None or len(inner_nav.named_children) < 2:
            break
        chain_verbs.append(inner_nav.named_children[-1].text.decode())
        root = inner_nav.named_children[0]
    receiver_type = None
    if root is not None and root.type == "identifier":
        receiver_type = var_types.get(root.text.decode())
    url_literal = None
    method_arg = None
    if args is not None:
        vargs = [va for va in args.named_children if va.type == "value_argument"]
        if vargs and vargs[0].named_children and vargs[0].named_children[0].type == "string_literal":
            url_literal = vargs[0].named_children[0].text.decode()
        if len(vargs) >= 2 and vargs[1].named_children:
            method_arg = http_method_of_arg(vargs[1].named_children[0].text.decode())
    return JvmHttpCall(
        receiver_type=receiver_type,
        call_name=call_name,
        url_literal=url_literal,
        chain_verbs=tuple(chain_verbs),
        method_arg=method_arg,
        start_line=_line(call.start_point),
        end_line=_line(call.end_point),
    )


def _kotlin_http_calls(root: TSNode, var_types: dict[str, str]) -> list[JvmHttpCall]:
    """Every ``call_expression`` in the file as a :class:`JvmHttpCall` record."""
    calls: list[JvmHttpCall] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            call = _kotlin_http_call(n, var_types)
            if call is not None:
                calls.append(call)
        stack.extend(n.named_children)
    return calls


# Node types that make an identifier's use an ASSEMBLED URL (concatenation), not a
# single clean hop -> the one-hop dataflow rejects a param used inside one (honest gap).
_KOTLIN_ASSEMBLY = frozenset(
    {"additive_expression", "multiplicative_expression", "binary_expression"}
)


def _kotlin_param_names(func: TSNode) -> list[str]:
    """Ordered parameter NAMES of a Kotlin function declaration."""
    params = next(
        (c for c in func.named_children if c.type == "function_value_parameters"), None
    )
    if params is None:
        return []
    names: list[str] = []
    for p in params.named_children:
        if p.type == "parameter":
            n = next((c for c in p.named_children if c.type == "identifier"), None)
            if n is not None:
                names.append(n.text.decode())
    return names


def _kotlin_path_arg_param(call: TSNode, param_names: set[str]) -> Optional[str]:
    """The param flowing as a BARE argument into a ``.path(...)`` builder call (Spring
    ``UriBuilder.path(String)``) inside ``call`` — the URL PATH specifically, preferred
    over params feeding elsewhere in the same uri lambda (``.build(pathVariables)``)."""
    stack = [call]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            nav = next(
                (c for c in n.named_children if c.type == "navigation_expression"), None
            )
            args = next(
                (c for c in n.named_children if c.type == "value_arguments"), None
            )
            if (
                nav is not None
                and len(nav.named_children) >= 2
                and nav.named_children[-1].text.decode() == "path"
                and args is not None
            ):
                for va in args.named_children:
                    if va.type == "value_argument":
                        for c in va.named_children:
                            if c.type in ("identifier", "simple_identifier") and (
                                c.text.decode() in param_names
                            ):
                                return c.text.decode()
        stack.extend(n.named_children)
    return None


def _kotlin_param_flow(call: TSNode, param_names: set[str]) -> Optional[str]:
    """The parameter whose identifier flows as a BARE argument into ``call`` (one hop).
    A param inside an additive/multiplicative expression is assembly -> None (gap)."""
    args = next((c for c in call.named_children if c.type == "value_arguments"), None)
    if args is None:
        return None
    # Prefer the param feeding a `.path(...)` builder over one flowing elsewhere into
    # the same uri lambda (e.g. `.build(pathVariables)`).
    path_param = _kotlin_path_arg_param(call, param_names)
    if path_param is not None:
        return path_param
    stack: list[tuple[TSNode, bool]] = [(args, False)]
    while stack:
        n, in_asm = stack.pop()
        asm = in_asm or n.type in _KOTLIN_ASSEMBLY
        if n.type in ("identifier", "simple_identifier") and not asm:
            if n.text.decode() in param_names:
                return n.text.decode()
        for c in n.named_children:
            stack.append((c, asm))
    return None


def _kotlin_uri_helper(
    func: TSNode, var_types: dict[str, str], import_fqn: dict[str, str]
) -> Optional[UriHelper]:
    """A Kotlin function as a one-hop :class:`UriHelper`, or None (mirrors _java_uri_helper)."""
    name = _name_of(func)
    if name is None:
        return None
    param_names = _kotlin_param_names(func)
    if not param_names:
        return None
    param_set = set(param_names)
    stack = [func]
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            hc = _kotlin_http_call(n, var_types)
            if hc is not None and hc.url_literal is None and call_verb(hc) is not None:
                origin = import_fqn.get(hc.receiver_type) if hc.receiver_type else None
                if is_recognized_call(hc.receiver_type or "", origin):
                    flowing = _kotlin_param_flow(n, param_set)
                    if flowing is not None:
                        return UriHelper(name, param_names.index(flowing), call_verb(hc))
        stack.extend(n.named_children)
    return None


def _kotlin_uri_helpers(
    root: TSNode, var_types: dict[str, str], import_fqn: dict[str, str]
) -> dict[str, UriHelper]:
    helpers: dict[str, UriHelper] = {}
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "function_declaration":
            h = _kotlin_uri_helper(n, var_types, import_fqn)
            if h is not None:
                helpers[h.name] = h
        stack.extend(n.named_children)
    return helpers


def _kotlin_helper_recoveries(
    root: TSNode, helpers: dict[str, UriHelper]
) -> list[DataflowRecovery]:
    """Recover a call for every UNQUALIFIED call site passing a string LITERAL at a known
    helper's flowing parameter index (one hop). An unqualified Kotlin call is a
    ``call_expression`` whose first named child is the bare name identifier."""
    out: list[DataflowRecovery] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "call_expression" and n.named_children:
            head = n.named_children[0]
            h = helpers.get(head.text.decode()) if head.type == "identifier" else None
            if h is not None:
                args = next(
                    (c for c in n.named_children if c.type == "value_arguments"), None
                )
                vargs = [c for c in args.named_children if c.type == "value_argument"] if args else []
                if len(vargs) > h.param_index:
                    inner = vargs[h.param_index].named_children
                    if inner and inner[0].type == "string_literal":
                        out.append(
                            DataflowRecovery(
                                verb=h.verb,
                                url_literal=inner[0].text.decode(),
                                start_line=_line(n.start_point),
                                end_line=_line(n.end_point),
                            )
                        )
        stack.extend(n.named_children)
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
