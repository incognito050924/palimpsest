"""Host-neutral dev-proxy config reader (wi_260713iah, mechanism A).

A front-end call to ``/api/...`` does not reach the back-end at that path directly:
a Vite/Svelte dev-proxy (``server.proxy``) forwards the ``/api`` prefix to a target,
optionally rewriting the path. To match a front-end ``ApiCall`` to a back-end
``Endpoint`` across tiers, the matcher must know how the proxy maps the prefix.

This module reads that mapping FROM THE REPO'S OWN config files with tree-sitter
(the existing ECMAScript parse path, ``extract/ecmascript.py`` / the ts+js grammars)
— it NEVER hardcodes any one repo's ``/api``->target rule. A DIFFERENT repo with a
DIFFERENT proxy prefix reads just as well (host-neutral).

Only the DECLARATIVE keys tree-sitter can read are honored. Two rewrite kinds:

  * :data:`KEEP` — no ``rewrite`` key: the prefix is forwarded unchanged, so the
    front-end ``/api/x`` matches a back-end ``/api/x`` directly (the readable,
    "literal" rewrite = identity).
  * :data:`GAP` — a FUNCTION-valued ``rewrite`` (``(p)=>p.replace(/^\\/api/,'')``) or
    an env-interpolated ``target`` (``process.env.X`` with no literal fallback):
    tree-sitter can read the KEY but cannot evaluate a JS function body or an env
    var, so we DEGRADE to an honest gap — no rewrite applied, and (crucially) the
    match is SUPPRESSED so we never assert a false cross-tier link. Never a silent
    wrong rewrite.

The result (:class:`ProxyRewrite`) exposes the single method the MATCH-level
route functions in ``ir.py`` consume: :meth:`ProxyRewrite.resolve` — duck-typed so
``ir.py`` (the base module) never imports this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Node as TSNode, Parser

# --- rewrite kinds -----------------------------------------------------------

# No ``rewrite`` key + a resolvable target: the prefix is forwarded unchanged.
KEEP = "keep"
# A function-valued ``rewrite`` OR an unresolvable (env-interpolated) target:
# tree-sitter cannot evaluate it -> honest gap, match suppressed (no false link).
GAP = "gap"

# Grammars for the config-file extensions (the ECMAScript parse path). Built once.
_TS_LANG = Language(tstypescript.language_typescript())
_JS_LANG = Language(tsjavascript.language())

# Config files a host-neutral reader looks for (Vite / SvelteKit dev servers).
_CONFIG_NAMES = (
    "vite.config.ts",
    "vite.config.js",
    "vite.config.mts",
    "vite.config.cts",
    "svelte.config.js",
)

# Value node types that make a ``rewrite`` a JS function -> unevaluable -> GAP.
_FN_TYPES = frozenset(
    {"arrow_function", "function", "function_expression", "function_declaration"}
)

# Directories never scanned for config (a vendored copy is not this repo's config).
_EXCLUDED_DIRS = {"node_modules", ".git", "dist", "build", ".svelte-kit", ".next"}


@dataclass(frozen=True)
class ProxyRule:
    """One ``server.proxy`` entry: ``prefix`` -> {``target``, rewrite ``kind``}.

    ``target`` is the literal proxy target when resolvable (a string or the literal
    fallback of an ``env.X || 'literal'`` chain), else None (env-only -> GAP).
    ``kind`` is :data:`KEEP` or :data:`GAP`.
    """

    prefix: str
    target: Optional[str]
    kind: str


@dataclass(frozen=True)
class ProxyRewrite:
    """The repo's declarative proxy-rewrite mapping (prefix -> rule).

    :meth:`resolve` is the ONLY thing the match-level route functions in ``ir.py``
    call — it maps a canonical route path to the path the back-end actually sees, or
    None to signal an unresolvable (GAP) prefix whose match must be suppressed.
    """

    rules: tuple[ProxyRule, ...] = ()

    def _match(self, canonical_path: str) -> Optional[ProxyRule]:
        """Longest proxy prefix that owns ``canonical_path`` (segment-aligned), or None."""
        best: Optional[ProxyRule] = None
        for r in self.rules:
            if canonical_path == r.prefix or canonical_path.startswith(r.prefix + "/"):
                if best is None or len(r.prefix) > len(best.prefix):
                    best = r
        return best

    def resolve(self, canonical_path: str) -> Optional[str]:
        """Match-level resolution of ``canonical_path`` under this proxy mapping.

        * Not under any proxy prefix -> returned unchanged (non-proxied call).
        * Under a :data:`KEEP` prefix -> unchanged (prefix forwarded as-is).
        * Under a :data:`GAP` prefix -> None (unresolvable; the caller suppresses the
          match so no false cross-tier link is asserted).
        """
        rule = self._match(canonical_path)
        if rule is None or rule.kind == KEEP:
            return canonical_path
        return None


def _str_inner(node: TSNode) -> Optional[str]:
    """Inner text of a ``string`` node (quotes stripped), or None."""
    for c in node.named_children:
        if c.type == "string_fragment":
            return c.text.decode()
    text = node.text.decode()
    if len(text) >= 2 and text[0] in "\"'`" and text[-1] == text[0]:
        return text[1:-1]
    return None


def _key_name(pair: TSNode) -> Optional[str]:
    """The property name of an object ``pair`` (identifier or string key)."""
    k = pair.child_by_field_name("key")
    if k is None:
        return None
    if k.type in ("property_identifier", "identifier"):
        return k.text.decode()
    if k.type == "string":
        return _str_inner(k)
    return None


def _find_pair(node: TSNode, name: str) -> Optional[TSNode]:
    """First ``pair`` keyed ``name`` in ``node``'s subtree (pre-order DFS)."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "pair" and _key_name(n) == name:
            return n
        stack.extend(reversed(n.named_children))
    return None


def _resolve_target(node: Optional[TSNode]) -> Optional[str]:
    """A literal proxy target, or None when it cannot be read statically.

    A plain string is the target. A template literal is the target only when it
    carries NO ``${...}`` interpolation. An ``A || B || 'literal'`` chain resolves to
    its first literal operand (the fallback pattern, e.g. ``env.X || 'http://...'``).
    A bare member/identifier (``process.env.X``) is unresolvable -> None -> GAP.
    """
    if node is None:
        return None
    t = node.type
    if t == "string":
        return _str_inner(node)
    if t == "template_string":
        if any(c.type == "template_substitution" for c in node.named_children):
            return None
        frags = [c.text.decode() for c in node.named_children if c.type == "string_fragment"]
        return "".join(frags)
    if t in ("binary_expression", "logical_expression"):
        op = node.child_by_field_name("operator")
        if op is not None and op.text.decode() == "||":
            for side in ("left", "right"):
                v = _resolve_target(node.child_by_field_name(side))
                if v is not None:
                    return v
    if t == "parenthesized_expression" and node.named_children:
        return _resolve_target(node.named_children[0])
    return None


def _rule_from_value(prefix: str, val: Optional[TSNode]) -> Optional[ProxyRule]:
    """Build a :class:`ProxyRule` from a proxy entry's value node."""
    if val is None:
        return None
    if val.type == "string":  # `'/api': 'http://localhost:8080'` shorthand -> KEEP
        return ProxyRule(prefix=prefix, target=_str_inner(val), kind=KEEP)
    if val.type == "object":
        target: Optional[str] = None
        has_fn_rewrite = False
        for p in val.named_children:
            if p.type != "pair":
                continue
            kn = _key_name(p)
            v = p.child_by_field_name("value")
            if kn == "target":
                target = _resolve_target(v)
            elif kn == "rewrite" and v is not None and v.type in _FN_TYPES:
                has_fn_rewrite = True
        # A function rewrite (unevaluable) OR an unresolvable target -> honest GAP.
        kind = GAP if (has_fn_rewrite or target is None) else KEEP
        return ProxyRule(prefix=prefix, target=target, kind=kind)
    return None


def _rules_from_tree(root: TSNode) -> list[ProxyRule]:
    """Every ``server.proxy`` entry declared in one parsed config tree."""
    server = _find_pair(root, "server")
    if server is None:
        return []
    server_obj = server.child_by_field_name("value")
    if server_obj is None:
        return []
    proxy = _find_pair(server_obj, "proxy")
    if proxy is None:
        return []
    proxy_obj = proxy.child_by_field_name("value")
    if proxy_obj is None or proxy_obj.type != "object":
        return []
    out: list[ProxyRule] = []
    for pair in proxy_obj.named_children:
        if pair.type != "pair":
            continue
        prefix = _key_name(pair)
        if not prefix or not prefix.startswith("/"):
            continue
        rule = _rule_from_value(prefix, pair.child_by_field_name("value"))
        if rule is not None:
            out.append(rule)
    return out


def _lang_for(suffix: str) -> Optional[Language]:
    if suffix in (".ts", ".mts", ".cts"):
        return _TS_LANG
    if suffix in (".js", ".cjs", ".mjs"):
        return _JS_LANG
    return None


def _is_vendored(path: Path, root: Path) -> bool:
    return any(part in _EXCLUDED_DIRS for part in path.relative_to(root).parts)


def read_proxy_rewrite(root: Path | str) -> ProxyRewrite:
    """Read the repo's Vite/Svelte dev-proxy rewrite mapping (host-neutral).

    Scans ``root`` for known config files, parses each with the matching grammar and
    collects its ``server.proxy`` entries. On a prefix declared in more than one
    config, the conservative :data:`GAP` wins (never silently pick a KEEP over an
    unresolvable one). Rules are returned prefix-sorted for a deterministic mapping.
    No config found -> an empty mapping (every path resolves to itself).
    """
    root = Path(root)
    rules: dict[str, ProxyRule] = {}
    for name in _CONFIG_NAMES:
        for path in sorted(root.rglob(name)):
            if not path.is_file() or _is_vendored(path, root):
                continue
            lang = _lang_for(path.suffix)
            if lang is None:
                continue
            tree = Parser(lang).parse(path.read_bytes())
            for rule in _rules_from_tree(tree.root_node):
                existing = rules.get(rule.prefix)
                if existing is None or (existing.kind == KEEP and rule.kind == GAP):
                    rules[rule.prefix] = rule
    return ProxyRewrite(tuple(rules[k] for k in sorted(rules)))
