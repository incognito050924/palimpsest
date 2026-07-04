"""외부에서 생성된 inferred RELATION을 KG에 적재한다(provider-free).

palimpsest는 LLM을 전혀 호출하지 않는다. inferred relation("A는 B에 인과적으로
관련된다 / 관련된다 / 충돌한다")은 외부 생성기가 주장한 것으로, 근거결박되고
멱등(idempotent)한 적재를 위해 :func:`load_relations`에 넘겨진다. Risk/DesignDecision과
달리 새 노드를 만들지 않는다 — 이미 존재하는 두 노드 사이의 순수 inferred 엣지다.
모든 엣지에 ``edge_kind='inferred'``를 찍어 결정론적 구조층과 분리(SEPARATE)를
유지한다(스키마가 강제하는 no-laundering 분리).

근거결박(entity-atomic). 양쪽 endpoint가 모두 실제 그래프 노드로 resolve돼야 하고
``rel_type``은 :data:`INFERRED_RELATION_TYPES` 중 하나여야 한다. resolve되지 않는
endpoint나 알 수 없는 rel_type이 하나라도 있으면 그 relation 전체를 이유와 함께
REJECT한다 — 매달린(dangling) 엣지도, 부분 적재도 없다. relation 하나를 거절해도
나머지는 멈추지 않는다. 결정론적 ``_REL_MERGE`` writer는 일부러 재사용하지 않는다:
그것은 ``edge_kind='deterministic'``를 찍어(이 inferred 층을 laundering하게 된다);
여기서는 모든 endpoint를 미리 resolve하고 불일치는 거절한다.

멱등(idempotence). 엣지는 ``(source, rel_type, target)`` 기준으로 MERGE되므로, 같은
payload를 다시 적재해도 아무것도 바뀌지 않는다.

신선도(freshness). ``code_bound_at``은 SOURCE endpoint의 ``committed_at``에 결박된다
(신선도는 생성기의 벽시계가 아니라 코드를 따른다). ``created_at``은 payload에 실려 온
외부 생성 시각이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from palimpsest.ir import EDGE_KIND_INFERRED, INFERRED_RELATION_TYPES, InferredRelation


@dataclass(frozen=True)
class RelationRejection:
    """거절된 relation과 그 이유 — 삼키지 않고 드러낸다."""

    key: str
    reason: str


@dataclass(frozen=True)
class RelationLoadResult:
    """적재 배치의 결과: 집계 수치 + 명시적 거절 이유."""

    intended: int
    loaded: int
    rejected: int
    rejections: tuple[RelationRejection, ...] = ()


# ``{rel}``은 닫힌 상수다(INFERRED_RELATION_TYPES에 속함을 검증), 데이터가 아니다 —
# kg/decision.py의 ``_EDGE_MERGE`` 템플릿과 동일한 방식. endpoint는 미리 resolve되므로
# 이 MATCH..MATCH..MERGE는 항상 materialize된다(조용한 no-op 없음).
_RELATION_MERGE = """
MATCH (a {{id: $source_id}})
MATCH (b {{id: $target_id}})
MERGE (a)-[e:`{rel}`]->(b)
SET e.edge_kind     = $edge_kind,
    e.source_commit = $source_commit,
    e.created_at    = $created_at,
    e.code_bound_at = $code_bound_at,
    e.generator     = $generator,
    e.model         = $model,
    e.confidence    = $confidence,
    e.semantic_verdict = $semantic_verdict
"""


def _key(r: InferredRelation) -> str:
    return f"{r.source_id}|{r.rel_type}|{r.target_id}"


def _structural_reject_reason(r: InferredRelation):
    """relation이 겉보기에 잘못된 이유, 또는 형식이 온전하면 None
    (endpoint 근거결박은 live 그래프를 대상으로 별도로 검사한다)."""
    if not (r.generator and r.generator.strip()):
        return "missing generator"
    if not (r.model and r.model.strip()):
        return "missing model"
    if r.rel_type not in INFERRED_RELATION_TYPES:
        return f"unknown rel_type '{r.rel_type}' (not in {sorted(INFERRED_RELATION_TYPES)})"
    return None


def _resolve(session, ids) -> set:
    if not ids:
        return set()
    rows = session.run(
        "UNWIND $ids AS id MATCH (n {id: id}) RETURN DISTINCT n.id AS id",
        ids=list(ids),
    )
    return {r["id"] for r in rows}


def _committed_at(session, node_id: str):
    rec = session.run(
        "MATCH (n {id: $id}) RETURN n.committed_at AS committed_at LIMIT 1",
        id=node_id,
    ).single()
    return rec["committed_at"] if rec else None


def _write(session, r: InferredRelation, code_bound_at) -> None:
    # Neo4j 속성은 map이 아니라 primitive/array라서, 외부 judge의 verdict는
    # JSON 문자열로 저장한다(recall에서 다시 파싱한다).
    semantic_verdict = (
        json.dumps(r.semantic_verdict, ensure_ascii=False)
        if r.semantic_verdict is not None
        else None
    )
    session.run(
        _RELATION_MERGE.format(rel=r.rel_type),
        source_id=r.source_id,
        target_id=r.target_id,
        edge_kind=EDGE_KIND_INFERRED,
        source_commit=r.source_commit,
        created_at=r.created_at,
        code_bound_at=code_bound_at,
        generator=r.generator,
        model=r.model,
        confidence=r.confidence,
        semantic_verdict=semantic_verdict,
    )


def load_relations(driver, relations) -> RelationLoadResult:
    """외부에서 생성된 inferred relation을 inferred KG 층에 적재한다.

    각 relation을 검증한 뒤(generator/model/알려진 rel_type), 양쪽 endpoint가
    모두 resolve되면 둘 사이에 inferred 엣지(``edge_kind='inferred'``)로 MERGE한다.
    형식이 잘못됐거나, 알 수 없는 rel_type을 갖거나, resolve되지 않는 endpoint가
    하나라도 있는 relation은 이유와 함께 REJECT한다(entity-atomic — 아무것도 쓰지
    않음); 나머지는 그대로 적재된다. intended/loaded/rejected 수치와 거절 이유를
    반환한다.
    """
    relations = list(relations)
    rejections: list[RelationRejection] = []
    loaded = 0
    with driver.session() as session:
        for r in relations:
            reason = _structural_reject_reason(r)
            if reason is not None:
                rejections.append(RelationRejection(_key(r), reason))
                continue

            # 근거결박은 미리 resolve한다(조용한 MATCH..MERGE no-op 없음): resolve되지
            # 않는 endpoint가 하나라도 있으면 relation 전체를 거절한다(entity-atomic).
            endpoints = {r.source_id, r.target_id}
            unresolved = sorted(endpoints - _resolve(session, endpoints))
            if unresolved:
                rejections.append(
                    RelationRejection(_key(r), f"unresolved endpoints: {unresolved}")
                )
                continue

            # 신선도는 SOURCE endpoint의 committed_at에 앵커된다(source가 코드
            # committed_at이 없는 inferred 엔티티면 None). endpoint별 신선도 정련은
            # 유예했다(Risk의 flags[0] 앵커와 동일).
            _write(session, r, _committed_at(session, r.source_id))
            loaded += 1
    return RelationLoadResult(
        intended=len(relations),
        loaded=loaded,
        rejected=len(rejections),
        rejections=tuple(rejections),
    )
