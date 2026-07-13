"""Static extraction of Svelte (.svelte) single-file components into the IR.

A ``.svelte`` file is markup, not ECMAScript, so this is a 2-level extractor
(the MA-A manual-traversal mechanism proven by the n3 spike on 956 real files):

  1. Parse the ``.svelte`` with the tree-sitter-svelte grammar. Each ``<script>``
     block is a ``script_element`` whose ``raw_text`` child carries a clean byte
     range for the block's source.
  2. For EACH ``script_element`` (Svelte 5 pairs a ``module`` script with the
     instance script — iterate ALL, never assume one):
       * read ``lang`` from the ``start_tag`` attributes (order-independent; absent
         = JavaScript; ``ts``/``typescript`` = TypeScript);
       * SKIP a block with a ``src`` attribute (external, e.g. a CDN) or a
         whitespace-only ``raw_text``;
       * slice ``raw_text`` bytes, re-parse with the TS grammar (``collect_types=
         True``) or the JS grammar (``collect_types=False``), and drive the REUSED
         ECMAScript :class:`~palimpsest.extract.ecmascript._EcmaWalker` over the
         sub-tree with ``line_offset`` so node lines AND call-site lines map back to
         the real ``.svelte`` line numbers.

All script blocks of one ``.svelte`` share ONE File node whose qualified_name is the
``.svelte`` repo-relative path (callables use that path as their modpath). CALLS and
DEPENDS_ON are resolved over the svelte fragment's OWN nodes only, via the shared
per-fragment resolvers — so they never cross the language-family boundary (SC-B).

This module owns NO structural or resolution logic of its own; it is a thin adapter
over the shared core (delegating to ``_EcmaWalker``, ``_scan_calls``, ``_calls_edges``
and ``_depends_on_edges``). Inner ``has_error`` on a re-parsed block is non-fatal
(tree-sitter error recovery). Never logs raw source (mirrors kotlin.py).
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import tree_sitter_svelte as tssvelte
from tree_sitter import Language, Parser, Node as TSNode

from palimpsest.ir import IR, Node, Edge, Provenance, FILE
from palimpsest.extract.ecmascript import (
    LangProfile,
    CallSite,
    _EcmaWalker,
    _scan_calls,
    _calls_edges,
    _depends_on_edges,
    _is_vendored,
    finalize_ir,
)
from palimpsest.extract.typescript import TS_PROFILES
from palimpsest.extract.javascript import JS_PROFILES

_SVELTE_LANG = Language(tssvelte.language())

# Reuse the EXACT profile objects (Language instance + name) the TS/JS extractors
# use, so the shared tags-query cache keyed on ``profile.name`` stays consistent.
# The spike routes a TS script through the PLAIN typescript grammar (a svelte script
# is never TSX). ``exts`` on the profile is irrelevant here (svelte does not iterate
# files by extension through ``extract_fragment``).
_TS_PROFILE: LangProfile = next(p for p in TS_PROFILES if p.name == "ts")
_JS_PROFILE: LangProfile = JS_PROFILES[0]

_TS_LANGS = {"ts", "typescript"}


def _start_tag(script_el: TSNode) -> TSNode | None:
    for c in script_el.named_children:
        if c.type == "start_tag":
            return c
    return None


def _raw_text(script_el: TSNode) -> TSNode | None:
    for c in script_el.named_children:
        if c.type == "raw_text":
            return c
    return None


def _attributes(start_tag: TSNode | None) -> dict[str, str | None]:
    """``{attribute_name: value}`` for a start_tag; a boolean attr (``module``) maps
    to ``None`` (present, no value). Order-independent."""
    attrs: dict[str, str | None] = {}
    if start_tag is None:
        return attrs
    for c in start_tag.named_children:
        if c.type != "attribute":
            continue
        name: str | None = None
        value: str | None = None
        for a in c.named_children:
            if a.type == "attribute_name":
                name = a.text.decode()
            elif a.type == "quoted_attribute_value":
                for q in a.named_children:
                    if q.type == "attribute_value":
                        value = q.text.decode()
        if name is not None:
            attrs[name] = value
    return attrs


def extract_svelte_fragment(root: Path | str, provenance: Provenance) -> IR:
    """Parse every ``*.svelte`` file under ``root`` into a raw IR fragment (no Repo
    node, no import resolution). CALLS/DEPENDS_ON are resolved over the fragment's
    OWN nodes only — the svelte family's CALLS scope."""
    root = Path(root)
    svelte_parser = Parser(_SVELTE_LANG)
    ts_parser = Parser(_TS_PROFILE.language)
    js_parser = Parser(_JS_PROFILE.language)

    nodes: list[Node] = []
    edges: list[Edge] = []
    call_sites: list[CallSite] = []
    type_refs: dict[str, set[str]] = defaultdict(set)

    for path in sorted(root.rglob("*.svelte")):
        if not path.is_file() or _is_vendored(path, root):
            continue  # skip vendored/build dirs (_EXCLUDED_DIRS) — repo's OWN source only
        source = path.read_bytes()
        rel = path.relative_to(root).as_posix()
        svelte_tree = svelte_parser.parse(source)

        # ONE File node per .svelte (shared by every script block); all script
        # blocks are walked with emit_file=False so they do not create their own.
        nodes.append(
            Node(
                kind=FILE,
                qualified_name=rel,
                name=path.name,
                provenance=provenance,
                path=rel,
                start_line=1,
                end_line=source.count(b"\n") + 1,
            )
        )

        for script_el in svelte_tree.root_node.named_children:
            if script_el.type != "script_element":
                continue
            attrs = _attributes(_start_tag(script_el))
            if "src" in attrs:
                continue  # external script (e.g. CDN) — no inline body to extract
            raw = _raw_text(script_el)
            if raw is None:
                continue
            script_bytes = source[raw.start_byte : raw.end_byte]
            if not script_bytes.strip():
                continue  # whitespace-only block

            lang = attrs.get("lang")  # absent -> None -> JavaScript
            if lang in _TS_LANGS:
                profile, parser = _TS_PROFILE, ts_parser
            else:
                profile, parser = _JS_PROFILE, js_parser

            # raw_text starts on the .svelte line where the block's source begins;
            # its 0-indexed start row == (script_start_line - 1) == the line_offset
            # that maps every sub-tree line back to the real .svelte line.
            line_offset = raw.start_point[0]
            sub_tree = parser.parse(script_bytes)  # inner has_error is non-fatal
            walker = _EcmaWalker(
                rel, script_bytes, sub_tree.root_node, provenance, profile, line_offset
            )
            walker.run(emit_file=False)
            nodes.extend(walker.nodes)
            edges.extend(walker.edges)
            for container, refs in walker.type_refs.items():
                type_refs[container].update(refs)
            call_sites.extend(
                _scan_calls(rel, sub_tree.root_node, profile, line_offset=line_offset)
            )

    edges.extend(_calls_edges(nodes, call_sites, provenance))
    edges.extend(_depends_on_edges(nodes, type_refs, provenance))
    return IR(nodes=nodes, edges=edges)


def extract(root: Path | str, provenance: Provenance, repo_name: str | None = None) -> IR:
    """Parse every ``*.svelte`` file under ``root`` into an :class:`IR`.

    Standalone (Svelte-only) extractor: resolves IMPORTS among the svelte files and
    adds a single Repo node. For a mixed TS/JS/Svelte repo use
    :func:`palimpsest.extract.extract_ecmascript` (union-wide import resolution).
    """
    root = Path(root)
    frag = extract_svelte_fragment(root, provenance)
    return finalize_ir(frag.nodes, frag.edges, repo_name or root.name, provenance)
