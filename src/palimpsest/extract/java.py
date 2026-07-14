"""Static extraction of Java source into the palimpsest IR.

Parser: tree-sitter-java (py-tree-sitter). Deterministic structural ontology only;
CALLS is resolved by the receiver's static type (per-language tags/locals queries)
with a name-based fallback when the receiver cannot be typed. Lombok-generated
members are invisible to a source parser — acceptable for v1.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Query, QueryCursor, Node as TSNode

from palimpsest.ir import IR, Node, Edge, Provenance
from palimpsest.ir import REPO, PACKAGE, FILE, CLASS, METHOD, ENDPOINT
from palimpsest.ir import CONTAINS, IMPORTS, CALLS, DEPENDS_ON, REALIZES, HANDLES
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
from palimpsest.extract.spring_config import resolve_base_url

_TYPE_DECLS = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration")
_METHOD_DECLS = ("method_declaration", "constructor_declaration")

_LANGUAGE = Language(tsjava.language())

# Per-language tree-sitter queries own the build-less spine (ADR-20260706 §결정6):
# `tags.scm` yields call references (with their receiver), `locals.scm` yields the
# typed bindings a receiver identifier is resolved against. A new language plugs in
# by adding its own queries/<lang>/*.scm; the resolver below stays language-agnostic.
_QUERY_DIR = Path(__file__).parent / "queries" / "java"
_TAGS_QUERY = Query(_LANGUAGE, (_QUERY_DIR / "tags.scm").read_text())
_LOCALS_QUERY = Query(_LANGUAGE, (_QUERY_DIR / "locals.scm").read_text())


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


# Grammar nodes wrapping a type declaration's supertypes: class `extends`
# (superclass), class `implements` (super_interfaces), interface `extends`
# (extends_interfaces). Each holds either a bare type or a type_list of types.
_SUPERTYPE_CONTAINERS = ("superclass", "super_interfaces", "extends_interfaces")


def _supertype_names(type_decl: TSNode) -> set[str]:
    """Simple names of the classes/interfaces ``type_decl`` extends or implements."""
    out: set[str] = set()
    for child in type_decl.named_children:
        if child.type not in _SUPERTYPE_CONTAINERS:
            continue
        refs = child.named_children[0].named_children if (
            child.named_children and child.named_children[0].type == "type_list"
        ) else child.named_children
        for ref in refs:
            name = _simple_type_name(ref)
            if name:
                out.add(name)
    return out


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


# --- test-impact marker (ADR-20260706 §결정6) --------------------------------
# A File/Class/Method node is test code if ANY of three deterministic signals
# holds (Java surface only): the file lives under ``src/test``, the file imports
# junit, or a declaration carries an ``@Test`` annotation. The marker is a node
# PROPERTY (never identity) — see ``ir.Node.is_test``.

def _is_test_path(rel_path: str) -> bool:
    """True iff ``rel_path`` runs through a ``src/<test-source-set>`` segment pair —
    Maven/standard-Gradle ``src/test/...`` AND Gradle ``*Test`` source sets
    (``src/commonTest``, ``src/jvmTest``, ``src/androidTest`` for Kotlin MPP / Android).
    A segment is a test source set iff it is ``test`` or ends in ``Test`` — so
    ``testFixtures`` (a fixtures source set) stays production, and a coincidental
    substring like ``foo_src/test`` never matches."""
    parts = rel_path.split("/")
    return any(
        parts[i] == "src" and (parts[i + 1] == "test" or parts[i + 1].endswith("Test"))
        for i in range(len(parts) - 1)
    )


def _is_junit_import(target: str) -> bool:
    """True for a junit import target: ``org.junit`` / ``org.junit.jupiter.*`` (junit
    4 & 5) or ``junit.framework.*`` (junit 3)."""
    return target.startswith("org.junit") or target.startswith("junit.")


def _string_literal_value(node: TSNode) -> str | None:
    """Inner text of a ``string_literal`` node (quotes stripped), else None."""
    if node.type != "string_literal":
        return None
    text = node.text.decode()
    return text[1:-1] if len(text) >= 2 else ""


def _flatten_string_literals(node: TSNode) -> list[str]:
    """String-literal values of a positional annotation argument — a bare literal, or
    every literal inside a ``{...}`` array (``@GetMapping({"/a","/b"})``). Non-literals
    are dropped."""
    lit = _string_literal_value(node)
    if lit is not None:
        return [lit]
    if node.type == "array_initializer":
        out: list[str] = []
        for child in node.named_children:
            out.extend(_flatten_string_literals(child))
        return out
    return []


def _annotation_info(ann: TSNode) -> AnnotationInfo | None:
    """Read one ``marker_annotation``/``annotation`` node into an :class:`AnnotationInfo`
    (simple name; positional string-literal args; named ``key=value`` value texts —
    string literals unquoted, other value expressions kept verbatim)."""
    if ann.type not in ("marker_annotation", "annotation"):
        return None
    name_node = ann.child_by_field_name("name")
    if name_node is None:
        return None
    name = name_node.text.decode().split(".")[-1]  # simple name (@a.b.Controller -> Controller)
    args: list[str] = []
    named: dict[str, str] = {}
    arglist = ann.child_by_field_name("arguments")
    if arglist is not None:
        for arg in arglist.named_children:
            if arg.type == "element_value_pair":
                key = arg.child_by_field_name("key")
                val = arg.child_by_field_name("value")
                if key is not None and val is not None:
                    lit = _string_literal_value(val)
                    named[key.text.decode()] = lit if lit is not None else val.text.decode()
            else:
                args.extend(_flatten_string_literals(arg))
    return AnnotationInfo(name=name, args=tuple(args), named_args=named)


def _annotations(decl_node: TSNode) -> list[AnnotationInfo]:
    """Every annotation on a class/method declaration's ``modifiers`` (the grammar-
    specific reader that feeds the shared ``spring`` mapper). Java places method
    return-type annotations (e.g. ``@ResponseBody`` in ``public @ResponseBody Map m()``)
    in ``modifiers`` too, so a legacy hybrid handler is captured here."""
    out: list[AnnotationInfo] = []
    for child in decl_node.named_children:
        if child.type != "modifiers":
            continue
        for ann in child.named_children:
            info = _annotation_info(ann)
            if info is not None:
                out.append(info)
    return out


def _has_test(anns: list[AnnotationInfo]) -> bool:
    """True if a declaration carries an ``@Test`` annotation (bare ``@Test``,
    ``@Test(...)``, or fully-qualified ``@org.junit.Test`` — all reduced to the simple
    name ``Test``)."""
    return any(a.name == "Test" for a in anns)


class _FileWalker:
    """Collects nodes + edges for a single parsed Java file."""

    def __init__(
        self,
        rel_path: str,
        source: bytes,
        root: TSNode,
        prov: Provenance,
        source_path: Path | None = None,
    ):
        self.rel_path = rel_path
        self.source = source
        self.root = root
        self.prov = prov
        # Absolute path of this file — the anchor config grounding walks up from to find
        # the module's ``application*.yaml`` (wi_260713iah ac-5). None disables grounding.
        self.source_path = source_path
        self.pkg = _package_fqn(root)
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        # class fqn -> tree-sitter body node (for later edge slices)
        self.class_bodies: dict[str, TSNode] = {}
        # class fqn -> referenced simple type names (fields + params + imports)
        self.class_refs: dict[str, set[str]] = {}
        # class fqn -> simple names of its direct supertypes (extends/implements)
        self.class_supertypes: dict[str, set[str]] = {}
        # simple names of single-type imports in this file (attributed to its classes)
        self.import_simple: set[str] = set()
        # single-type imports as simple-name -> FQN (narrows same-simple-name
        # cross-package collisions for CALLS/DEPENDS_ON resolution; wildcard imports
        # cannot narrow so are excluded). Java forbids two imports sharing a simple
        # name, so the map is unambiguous.
        self.import_qualified: dict[str, str] = {}
        # single-type import SIMPLE name -> resolved Java FQN (the HTTP scanner resolves
        # a receiver's declared type through this to a registered construct origin).
        # Holds the same simple->FQN data as import_qualified; a distinct feature reads
        # it (dedup of the two maps is a follow-up, kept separate here to not couple the
        # merge to a refactor).
        self.import_fqn: dict[str, str] = {}
        self.file_classes: list[str] = []
        # test-impact signals accumulated over the walk (see _is_test_path etc.).
        self.imports_junit = False
        self.has_test_annotation = False

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
        # File-level test-impact marker: stamp every File/Class/Method node this file
        # produced. is_test is a property (not identity), so post-hoc mutation of the
        # already-collected nodes is safe. Production (no signal) stays None.
        if _is_test_path(self.rel_path) or self.imports_junit or self.has_test_annotation:
            for n in self.nodes:
                n.is_test = True
        # JVM outbound-HTTP caller scan (wi_260713iah): a recognized literal-URL call
        # (receiver resolves via import to a registered construct) -> ApiCall node.
        # Runs last so ApiCall nodes are not is_test-stamped (mirrors ecmascript.py).
        var_types = _java_var_types(self.root)
        calls = _java_http_calls(self.root, var_types)
        self.nodes.extend(
            api_call_nodes(
                self._ground_base_urls(calls),
                self.import_fqn,
                self.rel_path,
                self.prov,
            )
        )
        # One-hop param->uri dataflow (wi_260713iah part 1): a helper whose parameter
        # flows into a recognized uri()/verb call, called with a LITERAL, recovers the
        # ApiCall the literal-callsite scan above deliberately left as a gap.
        helpers = _java_uri_helpers(self.root, var_types, self.import_fqn)
        if helpers:
            self.nodes.extend(
                dataflow_api_call_nodes(
                    _java_helper_recoveries(self.root, helpers), self.rel_path, self.prov
                )
            )

    def _ground_base_urls(self, calls: list[JvmHttpCall]) -> list[JvmHttpCall]:
        """Resolve each ``<field> + "/path"`` S2S call's base-url field to a target host.

        For a call whose URL is concatenated onto a caller field, look up that field's
        ``@Value("${...}")`` reference and ground it against the module's config
        (:func:`spring_config.resolve_base_url`). A resolved host is stamped onto the
        call (binding the target service); an ungrounded one leaves ``base_url`` None
        so the call emits no ApiCall (honest gap, ac-5/ac-6). Non-concatenated calls
        pass through untouched."""
        if not any(c.base_url_field for c in calls):
            return calls
        value_fields = _java_value_fields(self.root)
        out: list[JvmHttpCall] = []
        for call in calls:
            if call.base_url_field is not None and self.source_path is not None:
                ref = value_fields.get(call.base_url_field)
                host = resolve_base_url(self.source_path, ref) if ref else None
                call = replace(call, base_url=host)
            out.append(call)
        return out

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
                simple = target.split(".")[-1]
                self.import_simple.add(simple)
                self.import_qualified[simple] = target
                self.import_fqn[simple] = target
            if _is_junit_import(target):
                self.imports_junit = True

    def _type_decl(self, node: TSNode, enclosing_fqn: str | None, container_id: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        class_anns = _annotations(node)
        if _has_test(class_anns):
            self.has_test_annotation = True
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
                # Spring DI/stereotype role marker (design contract Decision 6): a pure
                # property off identity; None when the class carries no stereotype.
                role=spring_role(class_anns),
            )
        )
        # container (File or enclosing Class) CONTAINS this Class
        self._edge(CONTAINS, container_id, fqn)
        self.file_classes.append(fqn)
        self.class_refs.setdefault(fqn, set())
        self.class_supertypes[fqn] = _supertype_names(node)

        body = node.child_by_field_name("body")
        if body is None:
            return
        self.class_bodies[fqn] = body
        for member in body.named_children:
            if member.type in _METHOD_DECLS:
                self._method_decl(member, class_fqn=fqn, class_anns=class_anns)
            elif member.type in _TYPE_DECLS:
                self._type_decl(member, enclosing_fqn=fqn, container_id=fqn)
            elif member.type == "field_declaration":
                ref = _simple_type_name(member.child_by_field_name("type"))
                if ref:
                    self.class_refs[fqn].add(ref)

    def _method_decl(
        self, node: TSNode, class_fqn: str, class_anns: list[AnnotationInfo]
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        method_anns = _annotations(node)
        if _has_test(method_anns):
            self.has_test_annotation = True
        param_type_names = _param_types(node)
        params = ",".join(param_type_names)
        fqn = f"{class_fqn}#{name}({params})"
        # parameter types are dependencies of the declaring class (constructor-injection
        # params flow here too, so injected deps reach DEPENDS_ON — no new edge, ac-9)
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
        # Spring HTTP-API Endpoints (design contract Decisions 1 & 2): a controller
        # handler method realizes one or more Endpoints whose identity is the
        # ``spring:``-discriminated normalized route. Bind the File that REALIZES the
        # Endpoint and the handler Method that HANDLES it (SvelteKit routing-edge
        # precedent). A non-controller / view-returning method yields [].
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


# A resolved call site: (rel_path, call_line, method_name, receiver_kind, receiver_value).
# receiver_kind: "self" (unqualified / this), "name" (identifier receiver), "type"
# (new T()), or "other" (field-access / chain — unresolved, falls back).
CallSite = tuple[str, int, str, str, "str | None"]
# A typed binding: (rel_path, def_line, var_name, simple_type_name).
Binding = tuple[str, int, str, "str | None"]


class FileScan:
    """Call sites + typed bindings extracted from one file by the tags/locals queries."""

    def __init__(self) -> None:
        self.calls: list[CallSite] = []
        # fields bind at their declaring class; locals/params only inside their method.
        self.field_bindings: list[Binding] = []
        self.local_bindings: list[Binding] = []


_TYPE_BODY_NODES = ("class_body", "interface_body", "enum_body", "annotation_type_body")


def _binding_is_modeled(name_node: TSNode) -> bool:
    """True if the binding's enclosing type is one the walker turns into a node — a
    named top-level or member type. False for an anonymous class body or a method-local
    class anywhere in the enclosing chain, whose members the walker never sees; such
    bindings must be dropped so a local (e.g. inside a ``new Runnable(){...}``) cannot be
    attributed to the enclosing real method and shadow a field of the same name.

    The whole type chain up to the file root must be modeled — every enclosing type a
    direct member of the next — so a member type nested inside an anonymous/local class
    (which is itself unmodeled) is rejected too.
    """
    body = name_node.parent
    while body is not None and body.type not in _TYPE_BODY_NODES:
        body = body.parent
    if body is None:
        return False
    while body is not None:
        owner = body.parent
        if owner is None or owner.type == "object_creation_expression":
            return False  # this type body is an anonymous class
        a = owner.parent  # climb to the next enclosing type body
        while a is not None and a.type not in _TYPE_BODY_NODES:
            if a.type in ("method_declaration", "constructor_declaration"):
                return False  # a method-local type — never a walker node
            a = a.parent
        body = a
    return True


def _classify_receiver(recv: TSNode | None) -> tuple[str, str | None]:
    if recv is None or recv.type == "this":
        return ("self", None)
    if recv.type == "identifier":
        return ("name", recv.text.decode())
    if recv.type == "object_creation_expression":
        return ("type", _simple_type_name(recv.child_by_field_name("type")))
    return ("other", None)


def _scan_calls_and_bindings(rel_path: str, root: TSNode) -> FileScan:
    """Run the per-language tags/locals queries over one file's tree."""
    scan = FileScan()
    for _pat, caps in QueryCursor(_TAGS_QUERY).matches(root):
        names = caps.get("reference.call.name")
        if not names:  # a definition match, not a call reference
            continue
        call_node = caps["reference.call"][0]
        recv = caps.get("reference.call.receiver")
        kind, value = _classify_receiver(recv[0] if recv else None)
        scan.calls.append((rel_path, call_node.start_point[0] + 1, names[0].text.decode(), kind, value))

    for _pat, caps in QueryCursor(_LOCALS_QUERY).matches(root):
        for prefix, out in (("field", scan.field_bindings), ("local", scan.local_bindings)):
            d, t = caps.get(f"{prefix}.name"), caps.get(f"{prefix}.type")
            if d and t and _binding_is_modeled(d[0]):
                out.append((rel_path, d[0].start_point[0] + 1, d[0].text.decode(), _simple_type_name(t[0])))
    return scan


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


def _index_by_simple_name(nodes: list[Node], kind: str) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for n in nodes:
        if n.kind == kind:
            idx.setdefault(n.name, []).append(n.qualified_name)
    return idx


def _import_narrows_to(
    simple: str, candidates: list[str], file_imports: dict[str, str]
) -> str | None:
    """If the file's single-type imports qualify ``simple`` to exactly one of the
    corpus ``candidates`` (a same-simple-name cross-package collision), return that
    FQN; else None (no narrowing possible — collision stays ambiguous)."""
    fqn = file_imports.get(simple)
    return fqn if fqn in candidates else None


def _depends_on_edges(
    nodes: list[Node],
    class_refs: dict[str, set[str]],
    imports_by_path: dict[str, dict[str, str]],
    prov: Provenance,
) -> list[Edge]:
    by_simple = _index_by_simple_name(nodes, CLASS)
    class_path = {n.qualified_name: n.path for n in nodes if n.kind == CLASS}
    # Accumulator (constraint A): (src,dst) -> resolution, monotone typed>name join.
    # DEPENDS_ON has no typed path today, so every edge is "name"; the dict form keeps
    # dedup order-independent and mirrors the CALLS join.
    acc: dict[tuple[str, str], str] = {}
    for src_fqn, refs in class_refs.items():
        file_imports = imports_by_path.get(class_path.get(src_fqn, ""), {})
        for ref in refs:
            cands = list(by_simple.get(ref, ()))
            if len(cands) > 1:
                # ac-3: a simple name resolving to >1 same-simple-name class across
                # packages is disambiguated to the imported FQN when the file imports
                # it (drop the false edges to other-package same-name classes). Without
                # a narrowing import, keep all edges (no false negatives).
                narrowed = _import_narrows_to(ref, cands, file_imports)
                if narrowed is not None:
                    cands = [narrowed]
            for dst_fqn in cands:
                if dst_fqn == src_fqn:
                    continue
                acc.setdefault((src_fqn, dst_fqn), "name")
    return [
        Edge(kind=DEPENDS_ON, src=s, dst=d, provenance=prov, resolution=r)
        for (s, d), r in acc.items()
    ]


def _calls_edges(
    nodes: list[Node],
    call_sites: list[CallSite],
    field_bindings: list[Binding],
    local_bindings: list[Binding],
    class_supertypes: dict[str, set[str]],
    imports_by_path: dict[str, dict[str, str]],
    prov: Provenance,
) -> list[Edge]:
    """CALLS (Method->Method) resolved by the receiver's static type + hierarchy.

    When a call's receiver resolves to a known type, the call links to that type's
    method(s), to inherited methods on its supertypes, and to overriding methods on
    its known subtypes/implementors (reachability for test-impact, ADR-20260706).
    Unrelated same-named methods are excluded. Only when the receiver type cannot be
    determined does resolution fall back to name-based matching (preserving the prior
    recall). Self-loops are suppressed.
    """
    class_by_simple = _index_by_simple_name(nodes, CLASS)
    methods_by_class_name: dict[tuple[str, str], list[str]] = {}
    methods_by_name: dict[str, list[str]] = {}
    method_ranges: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    class_ranges: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for n in nodes:
        if n.kind == METHOD:
            cls = n.qualified_name.split("#", 1)[0]
            methods_by_class_name.setdefault((cls, n.name), []).append(n.qualified_name)
            methods_by_name.setdefault(n.name, []).append(n.qualified_name)
            method_ranges[n.path].append((n.start_line, n.end_line, n.qualified_name))
        elif n.kind == CLASS:
            class_ranges[n.path].append((n.start_line, n.end_line, n.qualified_name))

    # Type hierarchy (best-effort, resolved by simple name): direct super/sub edges.
    direct_super: dict[str, set[str]] = {}
    direct_sub: dict[str, set[str]] = defaultdict(set)
    for cfqn, snames in class_supertypes.items():
        supers = {s for sn in snames for s in class_by_simple.get(sn, ())}
        direct_super[cfqn] = supers
        for s in supers:
            direct_sub[s].add(cfqn)

    relatives_cache: dict[str, set[str]] = {}

    def _relatives(cfqn: str) -> set[str]:
        """``cfqn`` plus all transitive supertypes and subtypes in the corpus."""
        cached = relatives_cache.get(cfqn)
        if cached is not None:
            return cached
        out = {cfqn}
        for adj in (direct_super, direct_sub):
            stack = [cfqn]
            while stack:
                for nxt in adj.get(stack.pop(), ()):
                    if nxt not in out:
                        out.add(nxt)
                        stack.append(nxt)
        relatives_cache[cfqn] = out
        return out

    # Typed symbol tables: fields by declaring class, locals/params by their method.
    # Bindings inside anonymous/local classes were already dropped at scan time
    # (_binding_is_modeled), so a local can never be attributed to the enclosing method.
    sym_field: dict[str, dict[str, str]] = defaultdict(dict)
    for path, def_line, name, type_simple in field_bindings:
        if type_simple:
            cls = _innermost(class_ranges.get(path, []), def_line)
            if cls is not None:
                sym_field[cls][name] = type_simple
    sym_method: dict[str, dict[str, str]] = defaultdict(dict)
    for path, def_line, name, type_simple in local_bindings:
        if type_simple:
            owner = _innermost(method_ranges.get(path, []), def_line)
            if owner is not None:
                sym_method[owner][name] = type_simple

    # Accumulator (constraint A): (src,dst) -> resolution, with the monotone join
    # typed ∨ name = typed. An incoming "typed" UPGRADES a stored "name"; "name" never
    # downgrades a stored "typed". Emitting from the accumulated dict at the end makes
    # the marker independent of call-site source order (the same src->dst reachable from
    # both a typed and a name-fallback call site resolves "typed" either way). First-seen
    # key order preserves the prior deterministic edge order.
    acc: dict[tuple[str, str], str] = {}

    def _typed_seed(simple: str, file_imports: dict[str, str]) -> tuple[set[str], bool]:
        """Resolve a simple receiver/static type name to its corpus seed classes plus a
        precise flag. A same-simple-name cross-package collision (>1 candidate) that the
        file's imports narrow to one FQN is NARROWED to that class — the false edges to
        other-package same-name classes are DROPPED (ac-2 truthfulness), exactly as
        ``_depends_on_edges`` narrows DEPENDS_ON. The survivor is precisely typed. A
        collision that imports cannot narrow keeps ALL candidates but is a name guess
        (constraint B): precise=False so the edges are marked "name", NOT "typed" (recall
        unchanged — no import-qualification of the whole CALLS path, no overload
        resolution). A single candidate is precise as-is."""
        cands = list(class_by_simple.get(simple, ()))
        if len(cands) > 1:
            narrowed = _import_narrows_to(simple, cands, file_imports)
            if narrowed is not None:
                return {narrowed}, True
            return set(cands), False
        return set(cands), True

    for path, line, name, kind, value in call_sites:
        src = _innermost(method_ranges.get(path, []), line)
        if src is None:
            continue
        src_class = src.split("#", 1)[0]
        file_imports = imports_by_path.get(path, {})
        seed: set[str] = set()
        block_fallback = False  # a definitely-known receiver type never name-falls-back
        precise = True  # whether the seed's static type is precisely resolved
        if kind == "self":
            # unqualified / this: enclosing class, but a static import may resolve
            # elsewhere — allow name-based fallback if the hierarchy has no match.
            seed = {src_class}
        elif kind == "name":
            typ = sym_method[src].get(value) or sym_field[src_class].get(value)
            if typ:
                seed, precise = _typed_seed(typ, file_imports)
                block_fallback = True
            elif value in class_by_simple:  # static call on a corpus class: ClassName.m()
                seed, precise = _typed_seed(value, file_imports)
                block_fallback = True
        elif kind == "type" and value:  # new T().m()
            seed, precise = _typed_seed(value, file_imports)
            block_fallback = True

        candidates: set[str] = set()
        for c in seed:
            candidates |= _relatives(c)
        dsts: list[str] = []
        for tc in sorted(candidates):  # sorted -> deterministic edge order
            dsts.extend(methods_by_class_name.get((tc, name), ()))
        if dsts:
            # produced via the resolved-type / candidates path: typed unless the seed
            # was an unnarrowed simple-name collision (constraint B -> "name").
            resolution = "typed" if precise else "name"
        elif not block_fallback:
            dsts = list(methods_by_name.get(name, ()))  # NAME fallback (~603-604)
            resolution = "name"
        else:
            resolution = "name"  # no dsts; value unused

        for dst in dsts:
            if dst == src:
                continue
            key = (src, dst)
            prev = acc.get(key)
            if prev == "typed":
                continue  # typed never downgrades
            if prev is None or resolution == "typed":
                acc[key] = resolution
    return [
        Edge(kind=CALLS, src=s, dst=d, provenance=prov, resolution=r)
        for (s, d), r in acc.items()
    ]


# --- JVM outbound-HTTP caller scan (wi_260713iah) ----------------------------------
# The Java grammar half of the shared ``jvm_http`` recognizer: walk the tree into
# normalized ``JvmHttpCall`` records (chain-root receiver TYPE, verb/URL call shape)
# that the grammar-agnostic recognizer scores. kotlin.py mirrors this over its own AST.

def _java_var_types(root: TSNode) -> dict[str, str]:
    """Map each variable NAME (field / local / parameter) to its declared simple type
    name — the file-level symbol table a receiver identifier is resolved against.
    Last declaration wins (adequate for the literal-callsite scope)."""
    out: dict[str, str] = {}
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in ("field_declaration", "local_variable_declaration"):
            t = _simple_type_name(n.child_by_field_name("type"))
            if t:
                for c in n.named_children:
                    if c.type == "variable_declarator":
                        name = c.child_by_field_name("name")
                        if name is not None:
                            out[name.text.decode()] = t
        elif n.type == "formal_parameter":
            t = _simple_type_name(n.child_by_field_name("type"))
            name = n.child_by_field_name("name")
            if t and name is not None:
                out[name.text.decode()] = t
        stack.extend(n.named_children)
    return out


def _concat_base_url_arg(arg: TSNode) -> tuple[str, str] | None:
    """A ``<field> + "literal"`` base-url concatenation -> ``(field_name, url_literal)``.

    Recognizes the S2S shape ``baseUrl + "/portal/api/x"`` (an injected base-url field
    concatenated with a literal path): a ``binary_expression`` with a ``+`` operator, an
    identifier left operand, and a string-literal right operand. Returns the field name
    and the path literal (quotes kept, mirroring ``string_literal`` handling); else None
    (a non-``+`` expression, a literal+literal, or a non-identifier base is not this
    shape — grounding only applies to a single injected base-url field, wi_260713iah)."""
    if arg.type != "binary_expression":
        return None
    if arg.child_by_field_name("operator") is not None and arg.child_by_field_name("operator").text != b"+":
        return None
    left = arg.child_by_field_name("left")
    right = arg.child_by_field_name("right")
    if left is None or right is None:
        return None
    if left.type == "identifier" and right.type == "string_literal":
        return left.text.decode(), right.text.decode()
    return None


def _java_value_fields(root: TSNode) -> dict[str, str]:
    """Map each field NAME carrying ``@Value("${...}")`` to that annotation's inner
    string — the caller-side symbol table config grounding resolves a base-url field
    against (wi_260713iah ac-5). Only the referenced KEY is read; the value never flows
    through here (secret-exposure constraint is enforced in ``spring_config``)."""
    out: dict[str, str] = {}
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "field_declaration":
            ref = None
            for ann in _annotations(n):
                if ann.name == "Value" and ann.args:
                    ref = ann.args[0]
                    break
            if ref is not None:
                for c in n.named_children:
                    if c.type == "variable_declarator":
                        name = c.child_by_field_name("name")
                        if name is not None:
                            out[name.text.decode()] = ref
        stack.extend(n.named_children)
    return out


def _java_http_call(inv: TSNode, var_types: dict[str, str]) -> JvmHttpCall | None:
    """Read one ``method_invocation`` into a :class:`JvmHttpCall` (None if it has no name)."""
    name_node = inv.child_by_field_name("name")
    if name_node is None:
        return None
    # Walk the object chain to the root receiver, collecting the chain's verb methods.
    chain_verbs: list[str] = []
    obj = inv.child_by_field_name("object")
    while obj is not None and obj.type == "method_invocation":
        vn = obj.child_by_field_name("name")
        if vn is not None:
            chain_verbs.append(vn.text.decode())
        obj = obj.child_by_field_name("object")
    receiver_type = None
    if obj is not None and obj.type == "identifier":
        receiver_type = var_types.get(obj.text.decode())
    url_literal = None
    base_url_field = None
    method_arg = None
    args = inv.child_by_field_name("arguments")
    if args is not None:
        positional = args.named_children
        if positional and positional[0].type == "string_literal":
            url_literal = positional[0].text.decode()
        elif positional:
            concat = _concat_base_url_arg(positional[0])
            if concat is not None:
                base_url_field, url_literal = concat
        if len(positional) >= 2:
            method_arg = http_method_of_arg(positional[1].text.decode())
    return JvmHttpCall(
        receiver_type=receiver_type,
        call_name=name_node.text.decode(),
        url_literal=url_literal,
        chain_verbs=tuple(chain_verbs),
        method_arg=method_arg,
        start_line=_line(inv.start_point),
        end_line=_line(inv.end_point),
        base_url_field=base_url_field,
    )


def _java_http_calls(root: TSNode, var_types: dict[str, str]) -> list[JvmHttpCall]:
    """Every ``method_invocation`` in the file as a :class:`JvmHttpCall` record."""
    calls: list[JvmHttpCall] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "method_invocation":
            call = _java_http_call(n, var_types)
            if call is not None:
                calls.append(call)
        stack.extend(n.named_children)
    return calls


def _formal_param_names(method: TSNode) -> list[str]:
    """Ordered parameter NAMES of a method declaration (the helper's call-site index)."""
    params = method.child_by_field_name("parameters")
    if params is None:
        return []
    names: list[str] = []
    for p in params.named_children:
        if p.type in ("formal_parameter", "spread_parameter"):
            n = p.child_by_field_name("name")
            names.append(n.text.decode() if n is not None else "")
    return names


def _path_arg_param(args: TSNode, param_names: set[str]) -> Optional[str]:
    """The param flowing as a BARE argument into a ``.path(...)`` builder call (Spring
    ``UriBuilder.path(String)``) inside ``args`` — the URL PATH specifically, preferred
    over params feeding elsewhere in the same uri lambda (``.build(pathVariables)``)."""
    stack = [args]
    while stack:
        n = stack.pop()
        if n.type == "method_invocation":
            name_node = n.child_by_field_name("name")
            if name_node is not None and name_node.text.decode() == "path":
                pargs = n.child_by_field_name("arguments")
                if pargs is not None:
                    for c in pargs.named_children:
                        if c.type == "identifier" and c.text.decode() in param_names:
                            return c.text.decode()
        stack.extend(n.named_children)
    return None


def _param_flow(inv: TSNode, param_names: set[str]) -> Optional[str]:
    """The parameter whose identifier flows as a BARE argument into ``inv``'s call — the
    one-hop link (``uri(url)`` / ``b.path(url)``). A param used inside a ``binary_expression``
    (a concatenation ``base + url``) is ASSEMBLY, not one clean hop -> None (honest gap)."""
    args = inv.child_by_field_name("arguments")
    if args is None:
        return None
    # When the uri lambda has a `.path(...)` builder, the PATH param feeds it; prefer
    # that over any other param flowing elsewhere into the lambda (e.g. `.build(map)`).
    path_param = _path_arg_param(args, param_names)
    if path_param is not None:
        return path_param
    stack: list[tuple[TSNode, bool]] = [(args, False)]
    while stack:
        n, in_binary = stack.pop()
        binary = in_binary or n.type == "binary_expression"
        if n.type == "identifier" and not binary:
            t = n.text.decode()
            if t in param_names:
                return t
        for c in n.named_children:
            stack.append((c, binary))
    return None


def _java_uri_helper(
    method: TSNode, var_types: dict[str, str], import_fqn: dict[str, str]
) -> Optional[UriHelper]:
    """A method as a one-hop :class:`UriHelper`, or None. Qualifies when one of the
    method's parameters flows into a RECOGNIZED (registered-origin) uri()/verb HTTP call
    whose URL is NOT a call-site literal (the path comes one hop away)."""
    name_node = method.child_by_field_name("name")
    if name_node is None:
        return None
    param_names = _formal_param_names(method)
    if not param_names:
        return None
    param_set = set(param_names)
    stack = [method]
    while stack:
        n = stack.pop()
        if n.type == "method_invocation":
            hc = _java_http_call(n, var_types)
            if hc is not None and hc.url_literal is None and call_verb(hc) is not None:
                origin = import_fqn.get(hc.receiver_type) if hc.receiver_type else None
                if is_recognized_call(hc.receiver_type or "", origin):
                    flowing = _param_flow(n, param_set)
                    if flowing is not None:
                        return UriHelper(
                            name=name_node.text.decode(),
                            param_index=param_names.index(flowing),
                            verb=call_verb(hc),
                        )
        stack.extend(n.named_children)
    return None


def _java_uri_helpers(
    root: TSNode, var_types: dict[str, str], import_fqn: dict[str, str]
) -> dict[str, UriHelper]:
    """Every one-hop URL-forwarding helper in the file, keyed by method name."""
    helpers: dict[str, UriHelper] = {}
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in _METHOD_DECLS:
            h = _java_uri_helper(n, var_types, import_fqn)
            if h is not None:
                helpers[h.name] = h
        stack.extend(n.named_children)
    return helpers


def _java_helper_recoveries(
    root: TSNode, helpers: dict[str, UriHelper]
) -> list[DataflowRecovery]:
    """Recover a call for every same-file UNQUALIFIED call site that passes a string
    LITERAL at a known helper's flowing parameter index (strictly one hop)."""
    out: list[DataflowRecovery] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "method_invocation" and n.child_by_field_name("object") is None:
            name_node = n.child_by_field_name("name")
            h = helpers.get(name_node.text.decode()) if name_node is not None else None
            if h is not None:
                args = n.child_by_field_name("arguments")
                pos = list(args.named_children) if args is not None else []
                if len(pos) > h.param_index and pos[h.param_index].type == "string_literal":
                    out.append(
                        DataflowRecovery(
                            verb=h.verb,
                            url_literal=pos[h.param_index].text.decode(),
                            start_line=_line(n.start_point),
                            end_line=_line(n.end_point),
                        )
                    )
        stack.extend(n.named_children)
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
    class_supertypes: dict[str, set[str]] = {}
    call_sites: list[CallSite] = []
    field_bindings: list[Binding] = []
    local_bindings: list[Binding] = []
    imports_by_path: dict[str, dict[str, str]] = {}

    for path in _iter_java_files(root):
        source = path.read_bytes()
        tree = parser.parse(source)
        rel = path.relative_to(root).as_posix()
        walker = _FileWalker(rel, source, tree.root_node, provenance, source_path=path)
        walker.run()
        nodes.extend(walker.nodes)
        edges.extend(walker.edges)
        if walker.pkg:
            packages.add(walker.pkg)
        for fqn, refs in walker.class_refs.items():
            class_refs.setdefault(fqn, set()).update(refs)
        class_supertypes.update(walker.class_supertypes)
        imports_by_path[rel] = walker.import_qualified
        scan = _scan_calls_and_bindings(rel, tree.root_node)
        call_sites.extend(scan.calls)
        field_bindings.extend(scan.field_bindings)
        local_bindings.extend(scan.local_bindings)

    # DEPENDS_ON (Class->Class): resolve referenced simple type names against
    # known classes by unqualified name (best-effort, no full type resolution).
    edges.extend(_depends_on_edges(nodes, class_refs, imports_by_path, provenance))
    # CALLS (Method->Method): resolved by the receiver's static type + hierarchy.
    edges.extend(
        _calls_edges(
            nodes, call_sites, field_bindings, local_bindings,
            class_supertypes, imports_by_path, provenance,
        )
    )

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
