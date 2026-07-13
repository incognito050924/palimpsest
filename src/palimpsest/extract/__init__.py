"""Static extraction: source tree -> palimpsest IR (no Neo4j here).

Per-language extractors each own their ``queries/<lang>/*.scm`` (ADR-20260706
§결정6). ``extract`` stays the Java extractor (unchanged, backward-compatible);
Kotlin is reachable as ``extract_kotlin``; the ECMAScript family (TS/TSX/JS/JSX)
is reachable per-language (``extract_typescript``/``extract_javascript``) and, for
a mixed TS/JS repo, via the ``extract_ecmascript`` driver that concatenates the
per-family fragments into ONE IR with a union-wide IMPORTS resolution. All are
dispatchable via the ``EXTRACTORS_BY_EXT`` map keyed on source-file extension.
"""

from pathlib import Path

from palimpsest.ir import IR, Provenance
from palimpsest.extract.java import extract as extract_java
from palimpsest.extract.kotlin import extract as extract_kotlin
from palimpsest.extract.python import extract as extract_python
from palimpsest.extract.rust import extract as extract_rust
from palimpsest.extract.typescript import extract as extract_typescript, TS_PROFILES
from palimpsest.extract.javascript import extract as extract_javascript, JS_PROFILES
from palimpsest.extract.svelte import (
    extract as extract_svelte,
    extract_svelte_fragment,
)
from palimpsest.extract.ecmascript import extract_fragment, finalize_ir
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
    return finalize_ir(
        ts.nodes + js.nodes + svelte.nodes,
        ts.edges + js.edges + svelte.edges,
        repo_name or root.name,
        provenance,
    )


# Language dispatch by source-file extension.
EXTRACTORS_BY_EXT = {
    ".java": extract_java,
    ".kt": extract_kotlin,
    ".py": extract_python,
    ".rs": extract_rust,
    ".ts": extract_typescript,
    ".tsx": extract_typescript,
    ".js": extract_javascript,
    ".jsx": extract_javascript,
    ".svelte": extract_svelte,
}

__all__ = [
    "extract",
    "extract_java",
    "extract_kotlin",
    "extract_python",
    "extract_rust",
    "extract_typescript",
    "extract_javascript",
    "extract_svelte",
    "extract_ecmascript",
    "EXTRACTORS_BY_EXT",
    "read_provenance",
    "changed_paths",
]
