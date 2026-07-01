"""GraphRAG recall (n4-impl-recall): progressive, grounded, combinatorial.

The recall layer over the Knowledge Graph. It resolves a seed (a symbol
``qualified_name`` or a repo-relative file path), traverses the deterministic
ontology (CALLS / DEPENDS_ON / CONTAINS / IMPORTS) with a *bounded* depth and
result limit, and assembles the result by **combinatorial assembly only** — no
LLM / generative call anywhere on this path (a hard ac-3 invariant).
"""

from palimpsest.recall.graphrag import recall, expand

__all__ = ["recall", "expand"]
