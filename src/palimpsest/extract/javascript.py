"""Static extraction of JavaScript (.js) and JSX (.jsx) source into the IR.

Both extensions parse under the ONE javascript grammar and form ONE CALLS family.
All structural logic lives in the shared ECMAScript core
(:mod:`palimpsest.extract.ecmascript`). JS carries no type annotations, so every
identity paramType degrades to ``?``; ``collect_types=False`` (there are no types
to collect — JS never yields DEPENDS_ON, node n6's domain).
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_javascript as tsjavascript
from tree_sitter import Language

from palimpsest.ir import IR, Provenance
from palimpsest.extract.ecmascript import LangProfile, extract_fragment, finalize_ir

# JS family = .js + .jsx, both on the one javascript grammar, sharing a CALLS scope.
JS_PROFILES: list[LangProfile] = [
    LangProfile(name="js", exts=(".js", ".jsx"), language=Language(tsjavascript.language()), collect_types=False),
]


def extract(root: Path | str, provenance: Provenance, repo_name: str | None = None) -> IR:
    """Parse every ``*.js`` / ``*.jsx`` file under ``root`` into an :class:`IR`.

    Standalone (JS-only) extractor: resolves IMPORTS among the JS-family files and
    adds a single Repo node. For a mixed TS/JS repo use
    :func:`palimpsest.extract.extract_ecmascript` (union-wide import resolution).
    """
    root = Path(root)
    frag = extract_fragment(root, provenance, JS_PROFILES)
    return finalize_ir(frag.nodes, frag.edges, repo_name or root.name, provenance)
