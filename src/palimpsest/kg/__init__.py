"""Knowledge Graph ingest: extraction IR -> Neo4j (deterministic ontology).

Realizes the minimal, deterministic ontology in Neo4j and batch-ingests the
extraction ``IR`` idempotently (MERGE-on-id; git is SoT so the projection is
rebuildable) with git provenance + freshness stamping.
"""

from palimpsest.kg.community import (
    augment_communities,
    community_id,
    compute_communities,
)
from palimpsest.kg.decision import (
    DesignDecisionLoadResult,
    DesignDecisionRejection,
    decision_id,
    load_design_decisions,
)
from palimpsest.kg.ingest import create_constraints, ingest
from palimpsest.kg.risk import (
    RiskLoadResult,
    RiskRejection,
    load_risks,
    risk_id,
)
from palimpsest.kg.summary import (
    Rejection,
    SummaryLoadResult,
    load_summaries,
    summary_id,
)

__all__ = [
    "create_constraints",
    "ingest",
    "load_summaries",
    "summary_id",
    "SummaryLoadResult",
    "Rejection",
    "load_risks",
    "risk_id",
    "RiskLoadResult",
    "RiskRejection",
    "load_design_decisions",
    "decision_id",
    "DesignDecisionLoadResult",
    "DesignDecisionRejection",
    "augment_communities",
    "community_id",
    "compute_communities",
]
