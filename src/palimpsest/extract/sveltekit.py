"""SvelteKit route-detection pass: promote the framework's filesystem routing
convention into the deterministic Route/Endpoint/Layout/Hook ontology.

A SvelteKit route is NOT a tree-sitter symbol — it is a FILESYSTEM convention: a
``+``-prefixed file under ``src/routes/`` (and ``src/hooks.*``) defines a routing
node whose identity is a normalized URL. This pass therefore runs AFTER the
ECMAScript ts/js/svelte fragments are built (:func:`extract_ecmascript`): it reads
the already-extracted FILE nodes (one per source file, ``path`` = repo-relative
posix) and handler FUNCTION nodes (``name`` = the bare symbol, e.g. ``GET`` /
``load``) and derives the routing layer over them. It NEVER re-parses source and
NEVER diverts a file away from the existing extractors — the handler FUNCTIONs and
component scripts they emit stay untouched.

It returns ``(route_nodes, route_edges)`` for the caller to concat into
``finalize_ir`` (which passes non-IMPORTS edges through untouched), and, as a side
effect, stamps ``server_only=True`` on the already-collected FILE nodes it owns
(a pure PROPERTY post-mutation, mirroring the ``is_test`` precedent in java.py).

Ontology (all deterministic, path/filename-driven):
  * Route    — one per ``src/routes`` dir holding a ``+page.*`` file; id = URL.
  * Endpoint — one per HTTP-method handler in a ``+server.*``; id = ``"{METHOD} {url}"``.
  * Layout   — one per dir holding a ``+layout.*`` file; id = ``"layout:{url}"``.
  * Hook     — one per ``src/hooks.*``; id = ``"hook:{repo-relative path}"``.

Disclosed static lower-bound (do NOT claim completeness): a wrapped handler such as
``export const GET = protect(async () => {})`` is a ``call_expression`` value, so the
ECMAScript walker emits NO FUNCTION named ``GET``. Endpoint/HANDLES detection keys off
the handler FUNCTION, so that method's Endpoint (and its HANDLES) is absent — the
same gap applies to a wrapped ``load``. Plain ``export function GET`` / ``export const
load = async () => {}`` are detected.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict

from palimpsest.ir import (
    Node,
    Edge,
    Provenance,
    FILE,
    FUNCTION,
    ROUTE,
    ENDPOINT,
    LAYOUT,
    HOOK,
    REALIZES,
    HANDLES,
    LOADS,
    GUARDS,
)

_ROUTES_ROOT = "src/routes"
_GROUP_RE = re.compile(r"^\(.*\)$")

# The SvelteKit request-method exports a ``+server`` file may define (one Endpoint
# each). ``fallback`` catches any un-declared method.
_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "fallback"}
)

# Representative-file precedence (most -> least): the URL/lines come from the first
# present. +page.svelte/+layout.svelte are the canonical human entry; the +server /
# +page.server modules trail.
_PAGE_PRECEDENCE = ("+page.svelte", "+page.ts", "+page.js", "+page.server.ts")
_LAYOUT_PRECEDENCE = (
    "+layout.svelte",
    "+layout.ts",
    "+layout.js",
    "+layout.server.ts",
)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _under_routes(path: str) -> bool:
    return path == _ROUTES_ROOT or path.startswith(_ROUTES_ROOT + "/")


def _route_rel(dir_path: str) -> str:
    """The part of a route dir under ``src/routes`` (``''`` for src/routes itself)."""
    if dir_path == _ROUTES_ROOT:
        return ""
    return dir_path[len(_ROUTES_ROOT) + 1 :]


def _normalize_url(route_rel: str) -> str:
    """Relative dir -> the route URL: drop ``(group)`` segments entirely, keep every
    other segment LITERAL (``[slug]``/``[...rest]``/``[[opt]]``/``[id=matcher]`` are
    all preserved verbatim — stripping the matcher would collide two valid siblings).
    """
    segs = [s for s in route_rel.split("/") if s and not _GROUP_RE.match(s)]
    return "/" + "/".join(segs) if segs else "/"


def _is_descendant(ancestor_url: str, url: str) -> bool:
    """True when ``url`` is at or below ``ancestor_url`` in the normalized-URL tree."""
    if ancestor_url == "/":
        return True
    return url == ancestor_url or url.startswith(ancestor_url + "/")


def _rep_file(files: list[str], precedence: tuple[str, ...]) -> str:
    """The representative file among ``files`` by ``precedence``, deterministic."""
    for pref in precedence:
        for p in files:
            if _basename(p) == pref:
                return p
    return sorted(files)[0]


def _is_hook_file(path: str) -> bool:
    """A request-lifecycle hook at ``src/`` level (hooks.server.* / hooks.client.* /
    hooks.ts / hooks.js)."""
    if posixpath.dirname(path) != "src":
        return False
    b = _basename(path)
    return (
        b in ("hooks.ts", "hooks.js")
        or b.startswith("hooks.server.")
        or b.startswith("hooks.client.")
    )


def _is_server_hook(path: str) -> bool:
    return _basename(path).startswith("hooks.server.")


def _is_server_only(path: str) -> bool:
    """A server-only module: a ``.server.`` infix (``+page.server.ts`` /
    ``+layout.server.ts`` / ``hooks.server.ts``), a ``+server.*`` endpoint, or any
    file under ``src/lib/server/``."""
    if ".server." in path:
        return True
    if _basename(path).startswith("+server."):
        return True
    if path.startswith("src/lib/server/") or "/src/lib/server/" in path:
        return True
    return False


def extract_sveltekit_routes(
    nodes: list[Node], provenance: Provenance
) -> tuple[list[Node], list[Edge]]:
    """Derive the SvelteKit routing layer over already-extracted ``nodes``.

    Reads FILE + handler FUNCTION nodes; returns the new routing (nodes, edges) and,
    as a side effect, stamps ``server_only=True`` on the FILE nodes it owns.
    """
    file_nodes = [n for n in nodes if n.kind == FILE]
    file_by_path = {n.path: n for n in file_nodes if n.path}
    funcs_by_path: dict[str, list[Node]] = defaultdict(list)
    for n in nodes:
        if n.kind == FUNCTION and n.path:
            funcs_by_path[n.path].append(n)

    # ac-6: post-mutate the already-collected FILE nodes (pure PROPERTY, not identity).
    for fn in file_nodes:
        if fn.path and _is_server_only(fn.path):
            fn.server_only = True

    route_nodes: list[Node] = []
    route_edges: list[Edge] = []

    # Group every +-prefixed routing file by its directory.
    dir_files: dict[str, list[str]] = defaultdict(list)
    for path in file_by_path:
        if _under_routes(path) and _basename(path).startswith("+"):
            dir_files[posixpath.dirname(path)].append(path)

    server_layout_urls: list[str] = []

    for dir_path, paths in dir_files.items():
        url = _normalize_url(_route_rel(dir_path))

        # --- Route: a dir holding any +page.* file ---
        page_files = [p for p in paths if _basename(p).startswith("+page.")]
        if page_files:
            rep = _rep_file(page_files, _PAGE_PRECEDENCE)
            route_nodes.append(
                Node(
                    kind=ROUTE,
                    qualified_name=url,
                    name=url,
                    provenance=provenance,
                    path=rep,
                    start_line=1,
                    end_line=file_by_path[rep].end_line,
                )
            )
            # REALIZES: every +page.*/+error.* file in the dir -> the Route.
            for p in paths:
                b = _basename(p)
                if b.startswith("+page.") or b.startswith("+error."):
                    route_edges.append(Edge(REALIZES, p, url, provenance))
            # LOADS: a `load` FUNCTION in a +page script module (.ts/.js) -> the Route.
            for p in paths:
                b = _basename(p)
                if b.startswith("+page.") and not b.endswith(".svelte"):
                    for func in funcs_by_path.get(p, []):
                        if func.name == "load":
                            route_edges.append(
                                Edge(LOADS, func.qualified_name, url, provenance)
                            )

        # --- Endpoint: per HTTP-method handler in a +server.* file ---
        for p in paths:
            if not _basename(p).startswith("+server."):
                continue
            for func in funcs_by_path.get(p, []):
                if func.name in _HTTP_METHODS:
                    ep = f"{func.name} {url}"
                    route_nodes.append(
                        Node(
                            kind=ENDPOINT,
                            qualified_name=ep,
                            name=func.name,
                            provenance=provenance,
                            path=p,
                            start_line=func.start_line,
                            end_line=func.end_line,
                        )
                    )
                    route_edges.append(Edge(REALIZES, p, ep, provenance))
                    route_edges.append(Edge(HANDLES, func.qualified_name, ep, provenance))

        # --- Layout: a dir holding any +layout.* file ---
        layout_files = [p for p in paths if _basename(p).startswith("+layout.")]
        if layout_files:
            rep = _rep_file(layout_files, _LAYOUT_PRECEDENCE)
            lqn = f"layout:{url}"
            route_nodes.append(
                Node(
                    kind=LAYOUT,
                    qualified_name=lqn,
                    name=lqn,
                    provenance=provenance,
                    path=rep,
                    start_line=1,
                    end_line=file_by_path[rep].end_line,
                )
            )
            for p in layout_files:
                route_edges.append(Edge(REALIZES, p, lqn, provenance))
            if any(
                _basename(p) in ("+layout.server.ts", "+layout.server.js")
                for p in layout_files
            ):
                server_layout_urls.append(url)

    # --- Hook: src/hooks.* ---
    server_hook_qns: list[str] = []
    for path in file_by_path:
        if not _is_hook_file(path):
            continue
        hqn = f"hook:{path}"
        route_nodes.append(
            Node(
                kind=HOOK,
                qualified_name=hqn,
                name=_basename(path),
                provenance=provenance,
                path=path,
                start_line=1,
                end_line=file_by_path[path].end_line,
            )
        )
        route_edges.append(Edge(REALIZES, path, hqn, provenance))
        if _is_server_hook(path):
            server_hook_qns.append(hqn)

    # --- GUARDS (ac-4) ---
    page_route_urls = [n.qualified_name for n in route_nodes if n.kind == ROUTE]
    endpoint_qns = [n.qualified_name for n in route_nodes if n.kind == ENDPOINT]

    # A server Hook guards EVERY Endpoint AND EVERY page Route.
    for hqn in server_hook_qns:
        for eqn in endpoint_qns:
            route_edges.append(Edge(GUARDS, hqn, eqn, provenance))
        for rurl in page_route_urls:
            route_edges.append(Edge(GUARDS, hqn, rurl, provenance))

    # A server Layout guards EVERY descendant page Route, but NEVER an Endpoint
    # (keystone: a +server bypasses layout load). Ancestry is on normalized URLs.
    for lurl in server_layout_urls:
        lqn = f"layout:{lurl}"
        for rurl in page_route_urls:
            if _is_descendant(lurl, rurl):
                route_edges.append(Edge(GUARDS, lqn, rurl, provenance))

    return route_nodes, route_edges
