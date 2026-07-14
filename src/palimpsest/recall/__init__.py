"""GraphRAG recall (n4-impl-recall): progressive, grounded, combinatorial.

The recall layer over the Knowledge Graph. It resolves a seed (a symbol
``qualified_name`` or a repo-relative file path), traverses the deterministic
ontology (CALLS / DEPENDS_ON / CONTAINS / IMPORTS) with a *bounded* depth and
result limit, and assembles the result by **combinatorial assembly only** — no
LLM / generative call anywhere on this path (a hard ac-3 invariant).
"""

from palimpsest.recall.graphrag import (
    recall,
    recall_community,
    recall_risk,
    recall_decision,
    recall_semantic,
    recall_churn,
    recall_cochange,
    recall_test_impact,
    recall_edge_precision,
    recall_callgraph_locality,
    reconcile_recall,
    expand,
)
from palimpsest.recall.api_links import (
    recall_endpoint_callers,
    recall_call_endpoints,
)

__all__ = [
    "recall",
    "recall_community",
    "recall_risk",
    "recall_decision",
    "recall_semantic",
    "recall_churn",
    "recall_cochange",
    "recall_test_impact",
    "recall_edge_precision",
    "recall_callgraph_locality",
    "reconcile_recall",
    "expand",
    "recall_endpoint_callers",
    "recall_call_endpoints",
]
