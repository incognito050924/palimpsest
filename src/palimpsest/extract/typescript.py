"""Static extraction of TypeScript (.ts) and TSX (.tsx) source into the IR.

Both extensions form ONE CALLS family (a .tsx component may call a .ts util), each
routed to its own grammar: ``.tsx`` needs the tsx grammar (JSX), ``.ts`` the plain
typescript grammar. All structural logic lives in the shared ECMAScript core
(:mod:`palimpsest.extract.ecmascript`).

``collect_types=True``: the TS profile enables field/parameter type-annotation
collection so the shared core emits DEPENDS_ON (Class->Class / Module->Class, ac-4).
The JS profile keeps it False, so a JS fragment emits none — the asymmetry.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_typescript as tstypescript
from tree_sitter import Language, Query

from palimpsest.ir import IR, Provenance
from palimpsest.extract.ecmascript import LangProfile, extract_fragment, finalize_ir

# TS-only type-annotation query (ADR-20260706 §결정6). It references grammar node
# types absent from JavaScript (`public_field_definition`/`required_parameter`/
# `type_annotation`), so compiling it here at import is the STRUCTURAL guarantee of
# the DEPENDS_ON asymmetry: it loads only against the TS grammars (a JS build could
# not even parse the query). The walker collects the type refs structurally; this
# load-gate keeps the TS-only capture surface honest and versioned alongside.
_TYPES_TEXT = (Path(__file__).parent / "queries" / "typescript" / "types.scm").read_text()
_TS_LANG = Language(tstypescript.language_typescript())
_TSX_LANG = Language(tstypescript.language_tsx())
Query(_TS_LANG, _TYPES_TEXT)  # compile-gate against the typescript grammar
Query(_TSX_LANG, _TYPES_TEXT)  # compile-gate against the tsx grammar

# TS family = .ts (typescript grammar) + .tsx (tsx grammar), sharing a CALLS scope.
TS_PROFILES: list[LangProfile] = [
    LangProfile(name="ts", exts=(".ts",), language=_TS_LANG, collect_types=True),
    LangProfile(name="tsx", exts=(".tsx",), language=_TSX_LANG, collect_types=True),
]


def extract(root: Path | str, provenance: Provenance, repo_name: str | None = None) -> IR:
    """Parse every ``*.ts`` / ``*.tsx`` file under ``root`` into an :class:`IR`.

    Standalone (TS-only) extractor: resolves IMPORTS among the TS-family files and
    adds a single Repo node. For a mixed TS/JS repo use
    :func:`palimpsest.extract.extract_ecmascript` (union-wide import resolution).
    """
    root = Path(root)
    frag = extract_fragment(root, provenance, TS_PROFILES)
    return finalize_ir(frag.nodes, frag.edges, repo_name or root.name, provenance)
