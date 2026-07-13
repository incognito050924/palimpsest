"""Static extraction: source tree -> palimpsest IR (no Neo4j here).

Per-language extractors each own their ``queries/<lang>/*.scm`` (ADR-20260706
§결정6). ``extract`` stays the Java extractor (unchanged, backward-compatible);
Kotlin is reachable as ``extract_kotlin``; the ECMAScript family (TS/TSX/JS/JSX)
is reachable per-language (``extract_typescript``/``extract_javascript``) and, for
a mixed TS/JS repo, via the ``extract_ecmascript`` driver that concatenates the
per-family fragments into ONE IR with a union-wide IMPORTS resolution. Go is
reachable as ``extract_go`` (de-Class ontology: package top-level funcs + receiver
methods). All are dispatchable via the ``EXTRACTORS_BY_EXT`` map keyed on
source-file extension.
"""

from pathlib import Path

from palimpsest.ir import IR, Node, Provenance, REPO
from palimpsest.extract.java import extract as extract_java
from palimpsest.extract.kotlin import extract as extract_kotlin
from palimpsest.extract.python import extract as extract_python
from palimpsest.extract.go import extract as extract_go
from palimpsest.extract.rust import extract as extract_rust
from palimpsest.extract.typescript import extract as extract_typescript, TS_PROFILES
from palimpsest.extract.javascript import extract as extract_javascript, JS_PROFILES
from palimpsest.extract.svelte import (
    extract as extract_svelte,
    extract_svelte_fragment,
)
from palimpsest.extract.ecmascript import extract_fragment, finalize_ir
from palimpsest.extract.sveltekit import extract_sveltekit_routes
from palimpsest.extract.calls_api import CallEndpointMatch, RouteEnd, match_calls
from palimpsest.extract.provenance import changed_paths, read_provenance

# Backward-compatible default: `extract` remains the Java extractor.
extract = extract_java


def extract_ecmascript(
    root: Path | str, provenance: Provenance, repo_name: str | None = None
) -> IR:
    """Parse a mixed TS/TSX/JS/JSX repo under ``root`` into ONE :class:`IR`.

    Each language FAMILY (ts = .ts+.tsx, js = .js+.jsx, svelte = .svelte) is walked
    as an isolated fragment so name-based CALLS/DEPENDS_ON never cross the family
    boundary (SC-B). The fragments are concatenated in a fixed order (ts→js→svelte)
    and finalized together: a single union-wide ``_resolve_imports`` links a relative
    ``.ts``→``.js``→``.svelte`` import into one component WITHOUT a false CALLS, and
    ONE Repo node contains every File (no Package nodes).
    """
    root = Path(root)
    ts = extract_fragment(root, provenance, TS_PROFILES)
    js = extract_fragment(root, provenance, JS_PROFILES)
    svelte = extract_svelte_fragment(root, provenance)
    frag_nodes = ts.nodes + js.nodes + svelte.nodes
    frag_edges = ts.edges + js.edges + svelte.edges
    # SvelteKit routing pass: promote +-prefixed routing files (over the already-
    # extracted FILE + handler FUNCTION nodes) into Route/Endpoint/Layout/Hook and
    # stamp server_only on the FILE nodes it owns. The ts/svelte fragments above are
    # NOT diverted — handler FUNCTIONs / component scripts stay extracted. finalize_ir
    # passes the new non-IMPORTS routing edges through untouched.
    route_nodes, route_edges = extract_sveltekit_routes(frag_nodes, provenance)
    return finalize_ir(
        frag_nodes + route_nodes,
        frag_edges + route_edges,
        repo_name or root.name,
        provenance,
    )


# Language dispatch by source-file extension (the per-language registry: which
# extractor owns each extension). ``dispatch`` below routes by language GROUP, not
# by this raw per-extension map — see its docstring for why the ECMAScript family
# cannot be split into one-extractor-per-extension.
EXTRACTORS_BY_EXT = {
    ".java": extract_java,
    ".kt": extract_kotlin,
    ".py": extract_python,
    ".go": extract_go,
    ".rs": extract_rust,
    ".ts": extract_typescript,
    ".tsx": extract_typescript,
    ".js": extract_javascript,
    ".jsx": extract_javascript,
    ".svelte": extract_svelte,
}

# Language-GROUP dispatch table: (extensions, group extractor). Each extractor
# already walks the WHOLE tree scoped to ITS OWN extensions, resolves name-based
# CALLS/DEPENDS_ON over its own nodes only (SC-B, ADR-20260713), and returns a
# COMPLETE IR (its own Repo node). The ECMAScript family is ONE group precisely so
# ``extract_ecmascript`` keeps it a unified pass — union-wide relative-IMPORTS
# resolution (.ts<->.js) and the family CALLS boundary. A per-EXTENSION dispatch
# (extract_typescript for .ts, extract_javascript for .js separately) would split
# that pass and break both. Order is fixed -> deterministic merge order.
_LANGUAGE_GROUPS = (
    ((".java",), extract_java),
    ((".kt",), extract_kotlin),
    ((".py",), extract_python),
    ((".go",), extract_go),
    ((".rs",), extract_rust),
    ((".ts", ".tsx", ".js", ".jsx", ".svelte"), extract_ecmascript),
)


def _has_ext(root: Path, exts) -> bool:
    """True iff any file under ``root`` carries one of ``exts`` (cheap presence
    probe — first match short-circuits)."""
    return any(next(root.rglob(f"*{ext}"), None) is not None for ext in exts)


def dispatch(
    root: Path | str, provenance: Provenance, repo_name: str | None = None
) -> IR:
    """Extract a possibly-MIXED-language tree under ``root`` into ONE :class:`IR`.

    Each present language GROUP (see ``_LANGUAGE_GROUPS``) is extracted over
    ``root`` — every extractor self-scopes to its own extensions — and the
    fragments are concatenated in fixed group order under a SINGLE Repo node
    (each group's Repo node is deduped: they share ``repo_name`` + provenance, and
    every Repo->child CONTAINS edge already targets ``repo_name``, so collapsing to
    the first is exact). Cross-language name collisions never merge: each group's
    CALLS/DEPENDS_ON was resolved over its own nodes only (SC-B).

    A SINGLE-language tree reduces to that group's extractor verbatim — its Repo
    node moves to the front and the rest follow in order, byte-identical to calling
    the extractor directly (the Java regression invariant). An empty / unrecognized
    tree still yields a lone Repo node (parity with the per-language extractors,
    which always emit one).
    """
    root = Path(root)
    repo_name = repo_name or root.name
    repo_node: Node | None = None
    nodes: list[Node] = []
    edges = []
    for exts, extractor in _LANGUAGE_GROUPS:
        if not _has_ext(root, exts):
            continue
        ir = extractor(root, provenance, repo_name=repo_name)
        for n in ir.nodes:
            if n.kind == REPO:
                if repo_node is None:
                    repo_node = n
            else:
                nodes.append(n)
        edges.extend(ir.edges)
    if repo_node is None:
        repo_node = Node(
            kind=REPO, qualified_name=repo_name, name=repo_name, provenance=provenance
        )
    return IR(nodes=[repo_node] + nodes, edges=edges)

__all__ = [
    "extract",
    "extract_java",
    "extract_kotlin",
    "extract_python",
    "extract_go",
    "extract_rust",
    "extract_typescript",
    "extract_javascript",
    "extract_svelte",
    "extract_ecmascript",
    "dispatch",
    "EXTRACTORS_BY_EXT",
    "match_calls",
    "CallEndpointMatch",
    "RouteEnd",
    "read_provenance",
    "changed_paths",
]
