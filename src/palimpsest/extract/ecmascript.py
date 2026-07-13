"""Shared static-extraction core for the ECMAScript family (TS/TSX/JS/JSX).

Parser: tree-sitter-typescript (typescript + tsx grammars) and
tree-sitter-javascript (py-tree-sitter). Deterministic structural ontology only,
mirroring the Kotlin extractor (ADR-20260706 §결정6: per-language
``queries/<lang>/*.scm`` own the build-less tree-sitter spine; the resolver stays
language-neutral). ONE shared ``queries/ecmascript/tags.scm`` compiles against all
three grammars.

The family shares ONE walker (:class:`_EcmaWalker`). A per-family ``extract_fragment``
resolves name-based CALLS over ITS OWN nodes only — CALLS (and, later, DEPENDS_ON)
are name-based and would false-match across languages, so the driver NEVER re-runs a
union-wide CALLS pass. The only cross-language connection is IMPORTS: each walker
emits a raw ``Edge(IMPORTS, file, specifier)`` and the driver's union-wide
``_resolve_imports`` rewrites a relative specifier to the target File's
qualified_name (KEY DECISION 1+2 from the n2 design).

Identity (``qualified_name``, ac-6) is the module-path scheme:
  - Function : {modpath}.{name}({paramTypes})
  - Class    : {modpath}.{Class}
  - Method   : {classFqn}#{name}({paramTypes})
where ``modpath`` = repo-relative posix path (= the File node qualified_name) and
paramTypes = a simple TS type name or ``?`` (JS has none → every slot is ``?``).
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass

from tree_sitter import Language, Parser, Query, QueryCursor, Node as TSNode

from palimpsest.ir import IR, Node, Edge, Provenance
from palimpsest.ir import REPO, FILE, CLASS, METHOD, FUNCTION, API_CALL
from palimpsest.ir import CONTAINS, IMPORTS, CALLS, DEPENDS_ON
from palimpsest.ir import api_call_qualified_name
from palimpsest.extract.http_origins import is_recognized_call

# The shared per-family tags query (ADR-20260706 §결정6). Compiled once per grammar.
_QUERY_DIR = Path(__file__).parent / "queries" / "ecmascript"
_TAGS_TEXT = (_QUERY_DIR / "tags.scm").read_text()
_QUERY_CACHE: dict[str, Query] = {}

# Fixed relative-import extension probe order (deterministic first-match wins).
_IMPORT_EXTS = (".ts", ".tsx", ".js", ".jsx", ".svelte")


@dataclass(frozen=True)
class LangProfile:
    """One grammar's binding into the shared core.

    ``exts`` are the source extensions this grammar owns (``.js``/``.jsx`` both map
    to the javascript grammar). ``collect_types`` GATES type-annotation collection
    for DEPENDS_ON (Class->Class / Module->Class): the TS grammars set it True, the
    JS grammar keeps it False so a JS fragment emits zero DEPENDS_ON (the ac-4
    asymmetry — see ``queries/typescript/types.scm`` for the structural half).
    (Parameter-type names in the qualified_name are NOT gated by it; identity always
    uses them, degrading to ``?`` where a grammar has no annotation.)
    """

    name: str
    exts: tuple[str, ...]
    language: Language
    collect_types: bool = False


def _tags_query(profile: LangProfile) -> Query:
    q = _QUERY_CACHE.get(profile.name)
    if q is None:
        q = Query(profile.language, _TAGS_TEXT)
        _QUERY_CACHE[profile.name] = q
    return q


def _simple_type(node: TSNode | None) -> str:
    """Base (unqualified, un-generic, un-array) type name, or ``?`` when absent.

    ``node`` is a parameter's ``type_annotation`` (or None for an untyped param).
    """
    if node is None:
        return "?"
    t = node.type
    if t == "type_annotation":
        inner = node.named_children[0] if node.named_children else None
        return _simple_type(inner)
    if t in ("predefined_type", "type_identifier"):
        return node.text.decode()
    if t in ("generic_type", "array_type"):
        return _simple_type(node.named_children[0]) if node.named_children else "?"
    if t == "nested_type_identifier":
        return node.text.decode().split(".")[-1] or "?"
    return "?"


def _param_types(func: TSNode) -> list[str]:
    """Simple type names of ``func``'s parameters (positional, for identity).

    Handles the parenthesized ``formal_parameters`` form and the bare single-param
    arrow (``n => n``, exposed as the ``parameter`` field — always untyped → ``?``).
    """
    params = func.child_by_field_name("parameters")
    if params is None:
        if func.child_by_field_name("parameter") is not None:
            return ["?"]  # `n => ...` single un-parenthesized (untyped) arrow param
        return []
    out: list[str] = []
    for prm in params.named_children:
        # required_parameter / optional_parameter expose a `type` field
        # (type_annotation); bare JS identifiers / patterns have none → `?`.
        out.append(_simple_type(prm.child_by_field_name("type")))
    return out


def _first_arg(args: TSNode) -> TSNode | None:
    """The first positional argument expression of an ``arguments`` node, or None."""
    for c in args.named_children:
        return c
    return None


def _object_string_prop(obj: TSNode, key: str) -> str | None:
    """Value of ``obj``'s ``key`` property when it is a plain string literal, else None
    (e.g. the ``method: 'POST'`` of a ``fetch`` options object)."""
    for pair in obj.named_children:
        if pair.type != "pair":
            continue
        k = pair.child_by_field_name("key")
        v = pair.child_by_field_name("value")
        if k is not None and v is not None and k.text.decode() == key and v.type == "string":
            return v.text.decode()[1:-1]
    return None


def _fetch_method_and_url(args: TSNode) -> tuple[str, TSNode | None]:
    """``(method, url_arg)`` for a ``fetch``/``axios(url, opts)`` call.

    The URL is the first argument; the method is the ``method`` string of a second
    object argument (``{ method: 'POST' }``), defaulting to GET (the fetch default).
    """
    positional = args.named_children
    url_arg = positional[0] if positional else None
    method = "GET"
    if len(positional) >= 2 and positional[1].type == "object":
        m = _object_string_prop(positional[1], "method")
        if m:
            method = m.upper()
    return method, url_arg


def _literal_inner(node: TSNode) -> str | None:
    """Inner text of a string/template literal (quotes/backticks stripped), keeping a
    template's ``${…}`` interpolations verbatim; None for a non-literal operand."""
    if node.type in ("string", "template_string"):
        return node.text.decode()[1:-1]
    return None


def _concat_operands(node: TSNode) -> list[TSNode] | None:
    """Left-to-right operands of a ``+`` concatenation (flattening the left-associative
    ``a + b + c`` chain), or None when ``node`` is not a ``+`` binary expression."""
    op = node.child_by_field_name("operator")
    if op is None or op.text.decode() != "+":
        return None
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    operands: list[TSNode] = []
    if left is not None:
        sub = _concat_operands(left) if left.type == "binary_expression" else None
        operands.extend(sub if sub is not None else [left])
    if right is not None:
        operands.append(right)
    return operands


def _url_literal(arg: TSNode | None) -> str | None:
    """A raw URL string for ``api_call_qualified_name``, or None for a dynamic URL.

    A plain string / template literal is returned as-is (its own quotes/backticks let
    ``api_call_qualified_name`` template it). A ``+`` concatenation is rebuilt into a
    template literal — literal operands contribute their text, dynamic operands
    ``${_}`` — but ONLY when it carries at least one literal segment; a literal-free
    concat (``base + path``) and a bare variable are dynamic and return None (ac-6).
    """
    if arg is None:
        return None
    if arg.type in ("string", "template_string"):
        return arg.text.decode()
    if arg.type == "binary_expression":
        operands = _concat_operands(arg)
        if operands is None:
            return None
        parts: list[str] = []
        has_literal = False
        for op in operands:
            inner = _literal_inner(op)
            if inner is None:
                parts.append("${_}")
            else:
                parts.append(inner)
                has_literal = True
        if not has_literal:
            return None
        return "`" + "".join(parts) + "`"
    return None


class _EcmaWalker:
    """Collects structural nodes + CONTAINS/IMPORTS edges for one parsed source.

    Reused by the svelte extractor (n5), which slices a ``<script>`` block and
    re-walks it: ``line_offset`` shifts every node line to its real ``.svelte`` line,
    and ``emit_file=False`` suppresses the File node (all script blocks of one
    ``.svelte`` share the single File node the svelte extractor creates itself).
    """

    def __init__(
        self,
        rel_path: str,
        source: bytes,
        root: TSNode,
        prov: Provenance,
        profile: LangProfile,
        line_offset: int = 0,
    ):
        self.rel_path = rel_path
        self.source = source
        self.root = root
        self.prov = prov
        self.profile = profile
        self.line_offset = line_offset
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        # container fqn -> referenced simple type names (fields + params). Only
        # populated when ``profile.collect_types`` — the DEPENDS_ON source (n6, ac-4).
        self.type_refs: dict[str, set[str]] = defaultdict(set)
        # imported local NAME -> its raw import specifier (``axios`` -> ``axios``,
        # ``api`` -> ``./client``). Populated during the top-level pass; consumed by
        # HTTP recognition to resolve a call's callee ORIGIN (Frozen Invariant 5).
        self.local_imports: dict[str, str] = {}
        # ApiCall qualified_names already emitted in THIS file — same (method,path)
        # dedupes to one node (Decision 3).
        self._seen_api: set[str] = set()

    def _line(self, point) -> int:
        return point[0] + 1 + self.line_offset

    def _edge(self, kind: str, src: str, dst: str) -> None:
        self.edges.append(Edge(kind=kind, src=src, dst=dst, provenance=self.prov))

    def _fn_fqn(self, name: str, params: list[str]) -> str:
        return f"{self.rel_path}.{name}({','.join(params)})"

    def run(self, emit_file: bool = True) -> None:
        if emit_file:
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
        for child in self.root.named_children:
            self._top_level(child)
        # HTTP recognition runs AFTER the top-level pass so every import binding is
        # already in ``self.local_imports`` (a call's callee origin resolves against
        # the whole file's imports, wherever the call sits).
        self._scan_http_calls()

    def _top_level(self, node: TSNode) -> None:
        t = node.type
        if t == "function_declaration":
            self._function(node)
        elif t == "class_declaration":
            self._class(node)
        elif t == "lexical_declaration":
            self._lexical(node)
        elif t in ("import_statement", "export_statement"):
            # `import … from './x'` / `export … from './x'` / side-effect `import './x'`
            src_node = node.child_by_field_name("source")
            if src_node is not None:
                spec = self._string_value(src_node)
                if spec:
                    self._edge(IMPORTS, self.rel_path, spec)
                    # Only an `import` binds usable LOCAL callee names; a re-export
                    # (`export … from`) does not introduce a local value.
                    if t == "import_statement":
                        self._bind_imports(node, spec)
            # `export function/class/const …` — unwrap the inner declaration.
            if t == "export_statement":
                for c in node.named_children:
                    if c.type in ("function_declaration", "class_declaration", "lexical_declaration"):
                        self._top_level(c)

    def _bind_imports(self, stmt: TSNode, spec: str) -> None:
        """Bind each imported LOCAL name of ``stmt`` to its specifier ``spec``.

        Covers the three binding forms so a later call can resolve its callee's
        origin: default (``import axios from 'axios'``), namespace
        (``import * as http from 'node-fetch'``) and named, honoring aliases
        (``import { get as g } from 'axios'`` binds ``g``). Type-only imports bind
        no callable value but are harmless to record.
        """
        clause = next((c for c in stmt.named_children if c.type == "import_clause"), None)
        if clause is None:
            return
        for c in clause.named_children:
            if c.type == "identifier":  # default import
                self.local_imports[c.text.decode()] = spec
            elif c.type == "namespace_import":  # `* as name`
                for n in c.named_children:
                    if n.type == "identifier":
                        self.local_imports[n.text.decode()] = spec
            elif c.type == "named_imports":  # `{ a, b as c }`
                for isp in c.named_children:
                    if isp.type != "import_specifier":
                        continue
                    local = isp.child_by_field_name("alias") or isp.child_by_field_name("name")
                    if local is not None:
                        self.local_imports[local.text.decode()] = spec

    @staticmethod
    def _string_value(string_node: TSNode) -> str | None:
        for c in string_node.named_children:
            if c.type == "string_fragment":
                return c.text.decode()
        return None

    def _scan_http_calls(self) -> None:
        """Emit an ApiCall node for every recognized outbound-HTTP call in the file.

        Recognition keys off the callee's resolved import ORIGIN (Frozen Invariant 5,
        ``http_origins.is_recognized_call``), never the call syntax. A recognized
        member call ``base.verb(url, …)`` takes its method from the verb; a bare
        ``fetch(url, opts)`` from the ``method`` option (default GET). The identity is
        ``api_call_qualified_name(method, url)`` — emitting NOTHING when the URL is a
        non-templatable dynamic value (ac-6 disclosed gap).
        """
        for _pat, caps in QueryCursor(_tags_query(self.profile)).matches(self.root):
            call = caps.get("reference.http.call")
            base_cap = caps.get("reference.http.base")
            args_cap = caps.get("reference.http.args")
            if not call or not base_cap or not args_cap:
                continue
            base = base_cap[0].text.decode()
            if not is_recognized_call(base, self.local_imports.get(base)):
                continue
            verb_cap = caps.get("reference.http.verb")
            args = args_cap[0]
            if verb_cap:  # member call `base.verb(url, …)` — method is the verb
                method = verb_cap[0].text.decode().upper()
                url_arg = _first_arg(args)
            else:  # bare call `fetch(url, opts)` / `axios(url, opts)`
                method, url_arg = _fetch_method_and_url(args)
            raw_url = _url_literal(url_arg)
            if raw_url is None:
                continue
            qn = api_call_qualified_name(method, raw_url)
            if qn is None or qn in self._seen_api:
                continue  # dynamic URL (ac-6 gap) or same (method,path) already seen
            self._seen_api.add(qn)
            self.nodes.append(
                Node(
                    kind=API_CALL,
                    qualified_name=qn,
                    name=method,
                    provenance=self.prov,
                    path=self.rel_path,
                    start_line=self._line(call[0].start_point),
                    end_line=self._line(call[0].end_point),
                )
            )

    def _collect_param_refs(self, func: TSNode, container: str) -> None:
        """Record ``func``'s parameter types as dependencies of ``container`` (ac-4).

        Gated on ``collect_types`` (TS only). ``container`` is the enclosing Class
        fqn for a method, or the File/Module fqn for a top-level function (the
        de-Class generalization). Untyped (``?``) slots are skipped; primitive names
        (``string``/``number``/…) are recorded but self-filter at resolution — they
        match no Class node.
        """
        if not self.profile.collect_types:
            return
        for name in _param_types(func):
            if name != "?":
                self.type_refs[container].add(name)

    def _function(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:  # `export default function(){}` — anonymous
            return
        name = name_node.text.decode()
        fqn = self._fn_fqn(name, _param_types(node))
        self._add_callable(FUNCTION, fqn, name, node, container=self.rel_path)
        self._collect_param_refs(node, self.rel_path)

    def _lexical(self, node: TSNode) -> None:
        for vd in node.named_children:
            if vd.type != "variable_declarator":
                continue
            value = vd.child_by_field_name("value")
            if value is None or value.type not in ("arrow_function", "function_expression"):
                continue
            name_node = vd.child_by_field_name("name")
            if name_node is None or name_node.type != "identifier":
                continue  # destructuring / computed binding — no single symbol name
            name = name_node.text.decode()
            fqn = self._fn_fqn(name, _param_types(value))
            self._add_callable(FUNCTION, fqn, name, vd, container=self.rel_path)
            self._collect_param_refs(value, self.rel_path)

    def _class(self, node: TSNode) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        class_fqn = f"{self.rel_path}.{name}"
        self.nodes.append(
            Node(
                kind=CLASS,
                qualified_name=class_fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=self._line(node.start_point),
                end_line=self._line(node.end_point),
            )
        )
        self._edge(CONTAINS, self.rel_path, class_fqn)
        body = node.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:
            if member.type == "method_definition":
                self._method(member, class_fqn)
            elif member.type == "public_field_definition" and self.profile.collect_types:
                # A class field annotation `dep: Foo` -> Class DEPENDS_ON Foo (ac-4).
                ref = _simple_type(member.child_by_field_name("type"))
                if ref != "?":
                    self.type_refs[class_fqn].add(ref)

    def _method(self, node: TSNode, class_fqn: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None or name_node.type != "property_identifier":
            return  # computed / string-keyed member — no simple identity
        name = name_node.text.decode()
        fqn = f"{class_fqn}#{name}({','.join(_param_types(node))})"
        self.nodes.append(
            Node(
                kind=METHOD,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=self._line(node.start_point),
                end_line=self._line(node.end_point),
            )
        )
        self._edge(CONTAINS, class_fqn, fqn)
        # A method's parameter types are dependencies of its DECLARING class (ac-4).
        self._collect_param_refs(node, class_fqn)

    def _add_callable(self, kind: str, fqn: str, name: str, node: TSNode, container: str) -> None:
        self.nodes.append(
            Node(
                kind=kind,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=self._line(node.start_point),
                end_line=self._line(node.end_point),
            )
        )
        self._edge(CONTAINS, container, fqn)


# A resolved call site: (rel_path, call_line, callee_name).
CallSite = tuple[str, int, str]


def _scan_calls(rel_path: str, root: TSNode, profile: LangProfile, line_offset: int = 0) -> list[CallSite]:
    """Run the shared tags query over one file's tree for call references."""
    calls: list[CallSite] = []
    for _pat, caps in QueryCursor(_tags_query(profile)).matches(root):
        names = caps.get("reference.call.name")
        if not names:  # a definition match, not a call reference
            continue
        call_node = caps["reference.call"][0]
        calls.append((rel_path, call_node.start_point[0] + 1 + line_offset, names[0].text.decode()))
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
    simple name AMONG the fragment's own nodes. Self-loops are suppressed. Receiver
    typing is out of scope. Because ``nodes`` is a single fragment's nodes, a call
    never resolves across the language-family boundary (SC-B).
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
    nodes: list[Node], type_refs: dict[str, set[str]], prov: Provenance
) -> list[Edge]:
    """DEPENDS_ON (container -> Class) resolved name-based for this fragment.

    Mirrors ``java.py:_depends_on_edges``: each referenced simple type name is
    resolved against CLASS nodes by unqualified name (best-effort, no full type
    resolution). ``src`` is the ref's container — a Class fqn (class field / method
    param) or a File/Module fqn (top-level-function param, the de-Class
    generalization); ``dst`` is the matched Class. Self-loops and duplicates are
    suppressed. Because ``nodes`` is a SINGLE fragment's nodes, a TS ``x: Foo`` can
    never resolve to a ``class Foo`` in a different language family (SC-B).
    """
    by_simple = _index_by_simple_name(nodes, CLASS)
    seen: set[tuple[str, str]] = set()
    out: list[Edge] = []
    for src_fqn, refs in type_refs.items():
        for ref in refs:
            for dst_fqn in by_simple.get(ref, ()):
                if dst_fqn == src_fqn or (src_fqn, dst_fqn) in seen:
                    continue
                seen.add((src_fqn, dst_fqn))
                out.append(Edge(kind=DEPENDS_ON, src=src_fqn, dst=dst_fqn, provenance=prov))
    return out


# Vendored/build directories skipped by BOTH walks (this one and svelte.py's): a file
# is excluded when ANY segment of its path-relative-to-root is in this set, so "index
# the repo" means the repo's OWN source, not its dependencies or generated output.
_EXCLUDED_DIRS = {
    "node_modules", ".git", "dist", "build", ".svelte-kit", ".next", "out", "coverage",
}


def _is_vendored(path: Path, root: Path) -> bool:
    """True when ``path`` lies inside a vendored/build dir (any excluded path segment)."""
    return any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts)


def _iter_files(root: Path, exts: tuple[str, ...]):
    """All files under ``root`` with any of ``exts``, in a single deterministic order,
    excluding vendored/build directories (``_EXCLUDED_DIRS``)."""
    paths = [
        p
        for ext in exts
        for p in root.rglob(f"*{ext}")
        if p.is_file() and not _is_vendored(p, root)
    ]
    return sorted(paths)


def extract_fragment(root: Path | str, provenance: Provenance, profiles: list[LangProfile]) -> IR:
    """Parse one language FAMILY into a raw IR fragment (no Repo node, no import
    resolution). CALLS is resolved over the family's OWN nodes only.

    ``profiles`` is the set of grammars that share a CALLS scope (e.g. the ts family
    is [typescript(.ts), tsx(.tsx)]). Files are walked in fixed profile order, sorted
    within each — deterministic node/edge order.
    """
    root = Path(root)
    nodes: list[Node] = []
    edges: list[Edge] = []
    call_sites: list[CallSite] = []
    type_refs: dict[str, set[str]] = defaultdict(set)

    for profile in profiles:
        parser = Parser(profile.language)
        for path in _iter_files(root, profile.exts):
            source = path.read_bytes()
            tree = parser.parse(source)
            rel = path.relative_to(root).as_posix()
            walker = _EcmaWalker(rel, source, tree.root_node, provenance, profile)
            walker.run()
            nodes.extend(walker.nodes)
            edges.extend(walker.edges)
            for container, refs in walker.type_refs.items():
                type_refs[container].update(refs)
            call_sites.extend(_scan_calls(rel, tree.root_node, profile))

    edges.extend(_calls_edges(nodes, call_sites, provenance))
    # DEPENDS_ON is resolved over THIS fragment's own nodes only (never union-wide):
    # a collect_types=True family (TS) emits Class/Module->Class edges; a
    # collect_types=False family (JS) collected no type_refs -> emits none (ac-4).
    edges.extend(_depends_on_edges(nodes, type_refs, provenance))
    return IR(nodes=nodes, edges=edges)


def _resolve_specifier(importer: str, specifier: str, file_index: set[str]) -> str | None:
    if not (specifier.startswith("./") or specifier.startswith("../")):
        return None  # bare / aliased — left raw
    base = posixpath.dirname(importer)
    target = posixpath.normpath(posixpath.join(base, specifier))
    candidates = [target]  # specifier carried an explicit extension
    candidates += [target + ext for ext in _IMPORT_EXTS]
    candidates += [target + "/index" + ext for ext in _IMPORT_EXTS]
    for c in candidates:
        if c in file_index:
            return c
    return None


def _resolve_imports(edges: list[Edge], file_index: set[str]) -> list[Edge]:
    """Rewrite each IMPORTS edge's raw specifier dst to a resolved File qualified_name.

    A relative specifier (``./x``, ``../x``) is normalized against the importing
    file's directory and probed against the known File set with a fixed extension
    order (``.ts .tsx .js .jsx .svelte``) plus ``/index.*`` — first match wins. A
    bare / aliased specifier (``react``, ``@/foo``) or an unresolvable relative one
    is left raw (honest for a source-only parser — ir.py:172-178). Non-IMPORTS edges
    pass through untouched.
    """
    out: list[Edge] = []
    for e in edges:
        if e.kind != IMPORTS:
            out.append(e)
            continue
        target = _resolve_specifier(e.src, e.dst, file_index)
        dst = target if target is not None else e.dst
        out.append(Edge(kind=IMPORTS, src=e.src, dst=dst, provenance=e.provenance))
    return out


def _is_test_filename(rel_path: str) -> bool:
    """JS/TS/Svelte test-file convention: ``<name>.test.<ext>`` / ``<name>.spec.<ext>``."""
    parts = Path(rel_path).name.split(".")
    return len(parts) >= 3 and parts[-2] in ("test", "spec")


def _is_test_lib(spec: str) -> bool:
    """A jest / vitest / mocha import marks the importing file as test code."""
    return (
        spec in ("jest", "vitest", "mocha")
        or spec.startswith(("jest/", "vitest/", "mocha/", "@jest/"))
    )


def _mark_test_nodes(nodes: list[Node], edges: list[Edge]) -> None:
    """is_test marker (issue #17): a file is test code if its name matches the
    test convention OR it imports a test lib. Read from the RAW import specifiers
    (call BEFORE _resolve_imports rewrites them). Marks every code-unit node of a
    test file — pure PROPERTY, mirrors java.py. Covers TS/JS/Svelte (all funnel here).
    """
    test_paths = {n.path for n in nodes if n.kind == FILE and _is_test_filename(n.path)}
    for e in edges:
        if e.kind == IMPORTS and _is_test_lib(e.dst):
            test_paths.add(e.src)
    for n in nodes:
        if n.kind in (FILE, CLASS, METHOD, FUNCTION) and n.path in test_paths:
            n.is_test = True


def finalize_ir(nodes: list[Node], edges: list[Edge], repo_name: str, provenance: Provenance) -> IR:
    """Wrap concatenated fragment(s) into a full IR: union-wide IMPORTS resolution +
    ONE Repo node + Repo-CONTAINS->File per file (NO Package nodes — ECMAScript has
    no packages; community.py's containers are the Files carrying top-level Functions).
    """
    _mark_test_nodes(nodes, edges)  # BEFORE _resolve_imports (needs raw specifiers)
    file_index = {n.qualified_name for n in nodes if n.kind == FILE}
    edges = _resolve_imports(edges, file_index)
    repo = Node(kind=REPO, qualified_name=repo_name, name=repo_name, provenance=provenance)
    repo_edges = [
        Edge(kind=CONTAINS, src=repo_name, dst=fqn, provenance=provenance)
        for fqn in sorted(file_index)
    ]
    return IR(nodes=[repo] + nodes, edges=repo_edges + edges)
