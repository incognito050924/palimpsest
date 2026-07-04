"""GraphRAG 회상 (n4-impl-recall): 점진적(progressive)·근거결박(grounded)·조합적(combinatorial).

Knowledge Graph 위의 회상층. 씨앗(symbol의 ``qualified_name`` 또는 repo 상대 파일 경로)을
해석하고, 결정론적 온톨로지(CALLS / DEPENDS_ON / CONTAINS / IMPORTS)를 *한정된(bounded)* 깊이와
결과 개수 제한 안에서 순회하며, 결과를 **오직 조합적 조립(combinatorial assembly)만으로** 구성한다 —
이 경로 어디에도 LLM / 생성 호출이 없다(강한 ac-3 불변식).
"""

from palimpsest.recall.graphrag import (
    recall,
    recall_community,
    recall_risk,
    recall_decision,
    recall_semantic,
    recall_churn,
    recall_cochange,
    reconcile_recall,
    expand,
)

__all__ = [
    "recall",
    "recall_community",
    "recall_risk",
    "recall_decision",
    "recall_semantic",
    "recall_churn",
    "recall_cochange",
    "reconcile_recall",
    "expand",
]
