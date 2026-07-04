"""Knowledge Graph 적재: 추출 IR -> Neo4j (결정론적 온톨로지).

최소한의 결정론적 온톨로지를 Neo4j에 실현하고, 추출된 ``IR``을 멱등(idempotent)하게
배치 적재한다(MERGE-on-id; git이 SoT이므로 projection은 재구축 가능). git provenance와
신선도(freshness) 스탬프를 함께 남긴다.
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
from palimpsest.kg.ingest import create_constraints, ingest, ingest_modifies
from palimpsest.kg.relation import (
    RelationLoadResult,
    RelationRejection,
    load_relations,
)
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
    "ingest_modifies",
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
    "load_relations",
    "RelationLoadResult",
    "RelationRejection",
]
