"""외부에서 생성된 의미 요약을 KG로 적재한다 (provider-free).

palimpsest는 LLM을 전혀 호출하지 않는다. 요약은 외부 생성기가 만들어
:func:`load_summaries`에 넘겨 근거결박·멱등 적재를 맡긴다. 이 inferred 층은 두
표식으로 결정론적 구조층과 분리해 유지한다:

  * 노드 라벨 ``Summary`` (절대 코드 라벨이 아님), 그리고
  * 모든 ``SUMMARIZES`` 엣지의 ``edge_kind = "inferred"`` (결정론적 엣지는
    ``"deterministic"``) — 스키마로 강제되는 no-laundering 분리.

정직성(summary-atomic, 요약 단위 원자성). 각 claim은 실제 그래프 노드로 해결되는
``source_ref`` 1개 이상에 결박되어야 한다. 어느 검사든 실패한 요약은 이유와 함께
거부(REJECT)된다 — 절대 무음 드롭도, 부분 적재도 없다. 한 요약의 거부가 나머지
적재를 멈추지 않는다. 결정론적 ``_REL_MERGE`` writer(``MATCH..MATCH..MERGE``)는
의도적으로 재사용하지 않는다: 미해결 엔드포인트가 있으면 *무음으로* 아무것도 쓰지
않게 되어 이 계약을 위반한다; 여기서는 모든 엔드포인트를 미리 해결하고 불일치는
거부한다.

멱등성(Idempotence). Summary id는 결정론적이고 네임스페이스로 격리된다
(``summary:<hash>`` — 코드 ``qualified_name``과 절대 충돌하지 않는다). 모든 쓰기는
MERGE-on-id라서, 같은 payload를 다시 적재해도 아무것도 바뀌지 않는다.

신선도(Freshness). ``code_bound_at``은 해결된 TARGET 노드의 ``committed_at``에
결박된다 (신선도는 생성기의 벽시계가 아니라 코드를 따른다). ``created_at``은
payload에 실려 온 외부 생성 시각이다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from palimpsest.ir import EDGE_KIND_INFERRED, EMBEDDING_DIM, SUMMARY, Summary

# Summary 벡터 인덱스 (단일·닫힌 이름): EMBEDDING_DIM에 대한 cosine.
VECTOR_INDEX_NAME = "summary_embedding_cosine"


@dataclass(frozen=True)
class Rejection:
    """거부된 요약과 그 이유 — 드러내며, 절대 삼키지 않는다."""

    summary_id: str
    reason: str


@dataclass(frozen=True)
class SummaryLoadResult:
    """적재 배치의 결과: 개수 + 명시적 거부 이유들.

    ``embedded``는 적재된 요약 중 (유효한) 임베딩을 지닌 개수다;
    ``indexed``는 그중 지금 벡터 인덱스로 실제 질의 가능한 개수다 (인덱스가
    없거나 online이 아니면 0) — 이 둘이 무음으로 인덱싱되지 않은 벡터를 무음
    검색불가로 두지 않고 드러낸다.
    """

    intended: int
    loaded: int
    rejected: int
    rejections: tuple[Rejection, ...] = ()
    embedded: int = 0
    indexed: int = 0


def summary_id(target_id: str, generator: str, model: str, source_commit: str) -> str:
    """결정론적이고 네임스페이스로 격리된 Summary id.

    ``summary:`` 프리픽스가 코드 ``qualified_name``(Java FQN / repo path)과 절대
    같아질 수 없음을 보장하므로, 해시 우연 충돌이 나더라도 Summary가 코드 노드를
    가리지 않는다; 해시는 재적재를 멱등하게 만든다.
    """
    raw = "\x00".join([target_id, generator, model, source_commit])
    return "summary:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# 라벨("Summary")과 rel type("SUMMARIZES")은 쿼리 텍스트에 박아 넣은 닫힌 상수다;
# 요약 데이터의 모든 조각(id, 텍스트, refs, provenance)은 ``$params``로 실려 들어와,
# 적대적 claim 텍스트가 무해해진다.
_SUMMARY_MERGE = """
MERGE (s:Summary {id: $id})
SET s.target_id     = $target_id,
    s.claims        = $claims,
    s.generator     = $generator,
    s.model         = $model,
    s.source_commit = $source_commit,
    s.created_at    = $created_at,
    s.code_bound_at = $code_bound_at,
    s.confidence    = $confidence,
    s.semantic_verdict = $semantic_verdict,
    s.prompt        = $prompt,
    s.embedding     = $embedding,
    s.embedding_model = $embedding_model,
    s.embedding_dim = $embedding_dim
"""

# 엔드포인트는 위에서 미리 해결된다 (미해결 -> 요약 전체가 거부되며, 무음
# MATCH..MATCH..MERGE no-op은 절대 없다). 그래서 여기의 모든 MERGE는 구현된다.
_SUMMARIZES_MERGE = """
MATCH (s:Summary {id: $id})
UNWIND $targets AS tid
MATCH (t {id: tid})
MERGE (s)-[r:SUMMARIZES]->(t)
SET r.edge_kind     = $edge_kind,
    r.source_commit = $source_commit,
    r.created_at    = $created_at,
    r.code_bound_at = $code_bound_at,
    r.generator     = $generator,
    r.model         = $model,
    r.confidence    = $confidence
"""


def _structural_reject_reason(s: Summary):
    """요약이 겉으로 봐도 잘못된 이유, 또는 well-formed이면 None
    (refs의 근거결박은 라이브 그래프를 상대로 별도로 검사한다)."""
    if not (s.generator and s.generator.strip()):
        return "missing generator"
    if not (s.model and s.model.strip()):
        return "missing model"
    if not s.claims:
        return "0-claim summary"
    for i, claim in enumerate(s.claims):
        if not claim.source_refs:
            return f"claim {i} has no source ref"
    # 임베딩은 선택 사항이지만(하위 호환), 있으면 반드시 well-formed여야 한다:
    # 차원 검사는 벡터 인덱스 DDL과 동일한 EMBEDDING_DIM을 쓰므로, 차원이 틀린
    # 벡터는 Neo4j가 무음으로 건너뛰는 대신 여기서 거부된다.
    if s.embedding is not None:
        if len(s.embedding) != EMBEDDING_DIM:
            return (
                f"embedding dim mismatch: expected {EMBEDDING_DIM}, "
                f"got {len(s.embedding)}"
            )
        if not (s.embedding_model and s.embedding_model.strip()):
            return "embedding without embedding_model"
    return None


def _endpoints(s: Summary) -> set[str]:
    """해결되어야 하는 모든 노드 id: target과 모든 claim의 refs."""
    refs = {ref for claim in s.claims for ref in claim.source_refs}
    refs.add(s.target_id)
    return refs


def _resolve(session, ids: set[str]) -> set[str]:
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


# CommunityReport는 target이 Community 노드인 Summary다 — ``community:`` id
# 네임스페이스로 식별한다(kg.community.community_id을 미러링). 그런 리포트는 아래의
# 추가 멤버십-grounding 규칙을 진다.
_COMMUNITY_NS = "community:"


def _in_community(session, community_id: str, refs: set[str]) -> set[str]:
    """``refs`` 중 target Community에 속하는 id들 — 멤버 Class이거나, 멤버 Class가
    담고 있는 노드(예: 멤버의 Method).

    CommunityReport의 멤버십-grounding을 강제한다: 어떤 community에 관한 리포트는
    임의의 코드가 아니라 그 community의 멤버에 claim을 결박해야 한다. 여기서 반환되지
    않은 refs는 비멤버이며 리포트 전체를 거부시킨다.
    """
    if not refs:
        return set()
    rows = session.run(
        """
        UNWIND $refs AS rid
        MATCH (n {id: rid})
        OPTIONAL MATCH (n)-[:MEMBER_OF]->(dc:Community {id: $cid})
        OPTIONAL MATCH (owner:Class)-[:CONTAINS]->(n)
        OPTIONAL MATCH (owner)-[:MEMBER_OF]->(oc:Community {id: $cid})
        WITH rid, dc, oc
        WHERE dc IS NOT NULL OR oc IS NOT NULL
        RETURN DISTINCT rid AS id
        """,
        refs=list(refs),
        cid=community_id,
    )
    return {r["id"] for r in rows}


def _write(session, sid: str, s: Summary, endpoints: set[str], code_bound_at) -> None:
    claims = [json.dumps(c.to_dict(), ensure_ascii=False) for c in s.claims]
    # Neo4j 속성은 맵이 아니라 원시값/배열이므로, 외부 judge의 verdict는 (claims처럼)
    # JSON 문자열로 저장하고, 회상 시 다시 파싱한다.
    semantic_verdict = (
        json.dumps(s.semantic_verdict, ensure_ascii=False)
        if s.semantic_verdict is not None
        else None
    )
    session.run(
        _SUMMARY_MERGE,
        id=sid,
        target_id=s.target_id,
        claims=claims,
        generator=s.generator,
        model=s.model,
        source_commit=s.source_commit,
        created_at=s.created_at,
        code_bound_at=code_bound_at,
        confidence=s.confidence,
        semantic_verdict=semantic_verdict,
        prompt=s.prompt,
        embedding=s.embedding,
        embedding_model=s.embedding_model,
        embedding_dim=s.embedding_dim,
    )
    session.run(
        _SUMMARIZES_MERGE,
        id=sid,
        targets=sorted(endpoints),
        edge_kind=EDGE_KIND_INFERRED,
        source_commit=s.source_commit,
        created_at=s.created_at,
        code_bound_at=code_bound_at,
        generator=s.generator,
        model=s.model,
        confidence=s.confidence,
    )


# EMBEDDING_DIM과 'cosine'은 (박아 넣은 Summary 라벨처럼) 신뢰된 내부 상수이며,
# 절대 payload 데이터가 아니다 — DDL 텍스트에 인라인해도 안전하다.
_CREATE_VECTOR_INDEX = (
    f"CREATE VECTOR INDEX `{VECTOR_INDEX_NAME}` IF NOT EXISTS "
    f"FOR (s:`{SUMMARY}`) ON (s.embedding) "
    f"OPTIONS {{indexConfig: {{"
    f"`vector.dimensions`: {EMBEDDING_DIM}, "
    f"`vector.similarity_function`: 'cosine'}}}}"
)


def create_vector_index(driver) -> None:
    """Summary 임베딩 VECTOR INDEX를 마련한다 (cosine, EMBEDDING_DIM).

    ``create_constraints``를 미러링해 멱등하다(``IF NOT EXISTS``). Neo4j는 벡터
    인덱스를 비동기로 채우므로, 이 함수는 인덱스가 ONLINE에 도달하기를 기다린다
    (AWAIT): 아직 POPULATING 중인 인덱스에 곧바로 질의하면 부분/빈 결과가 나온다.
    """
    with driver.session() as session:
        session.run(_CREATE_VECTOR_INDEX)
        # 모든 인덱스(이것 포함)의 채우기가 끝날 때까지 블록한다.
        session.run("CALL db.awaitIndexes($timeout)", timeout=300)


def _index_online(session, name: str) -> bool:
    rec = session.run(
        "SHOW INDEXES YIELD name, state WHERE name = $n RETURN state",
        n=name,
    ).single()
    return rec is not None and rec["state"] == "ONLINE"


def _indexed_count(session) -> int:
    """지금 벡터 인덱스로 질의 가능한, 임베딩을 지닌 Summary 노드가 몇 개인지.

    인덱스가 없거나 online이 아니면 0. 그렇지 않으면 k>=total인 queryNodes 프로브가
    인덱싱된 모든 노드를 반환한다(queryNodes는 점수와 무관하게 최대 k개를 낸다).
    그래서 중복 없는 히트 수를 세면 실제로 인덱싱된 총계가 나온다 — Neo4j가 무음으로
    인덱싱에 실패한 벡터를 잡아낸다(무음 검색불가 가시성)."""
    if not _index_online(session, VECTOR_INDEX_NAME):
        return 0
    session.run("CALL db.awaitIndexes($timeout)", timeout=300)
    total = session.run(
        "MATCH (s:Summary) WHERE s.embedding IS NOT NULL RETURN count(s) AS c"
    ).single()["c"]
    if total == 0:
        return 0
    rows = session.run(
        "CALL db.index.vector.queryNodes($name, $k, $probe) "
        "YIELD node RETURN count(DISTINCT node) AS c",
        name=VECTOR_INDEX_NAME,
        k=total,
        probe=[1.0] * EMBEDDING_DIM,
    ).single()
    return rows["c"] if rows else 0


def _established_embedding_model(session):
    """그래프의 어떤 Summary에든 이미 결박된 embedding_model, 또는 None.

    cosine 벡터 인덱스는 단일-model이다 — 다른 model의 벡터를 비교하는 것은 차원이
    같아도 무의미하다 — 그래서 처음 적재된 model이 인덱스의 model을 확립하고, 이후
    적재는 그것과 일치해야 한다."""
    rec = session.run(
        "MATCH (s:Summary) WHERE s.embedding_model IS NOT NULL "
        "RETURN s.embedding_model AS m LIMIT 1"
    ).single()
    return rec["m"] if rec else None


def load_summaries(driver, summaries) -> SummaryLoadResult:
    """외부에서 생성된 요약을 inferred KG 층으로 적재한다.

    각 요약을 검증한 뒤, 근거결박되면 해결된 target/refs로 향하는 ``SUMMARIZES``
    엣지(``edge_kind='inferred'``)를 단 ``Summary`` 노드로 MERGE한다. 잘못됐거나
    refs가 전부 해결되지 않는 요약은 이유와 함께 거부된다(summary-atomic — 그 claim은
    하나도 적재되지 않는다); 나머지는 그대로 적재된다. intended/loaded/rejected 개수와
    거부 이유들을 반환한다.
    """
    summaries = list(summaries)
    rejections: list[Rejection] = []
    loaded = 0
    embedded = 0

    with driver.session() as session:
        # 인덱스에 이미 확립된 model (이전 적재에서); 아직 없으면 이 배치의 첫
        # 임베딩 요약이 그것을 확립한다.
        established_model = _established_embedding_model(session)
        for s in summaries:
            sid = summary_id(s.target_id, s.generator, s.model, s.source_commit)

            reason = _structural_reject_reason(s)
            if reason is not None:
                rejections.append(Rejection(sid, reason))
                continue

            # 인덱스당 단일 임베딩 model: well-formed 임베딩이라도 model이 확립된
            # 것과 다르면 거부된다 (나머지는 그대로 적재된다).
            if s.embedding is not None:
                if established_model is None:
                    established_model = s.embedding_model
                elif s.embedding_model != established_model:
                    rejections.append(
                        Rejection(
                            sid,
                            f"embedding model mismatch: index established with "
                            f"'{established_model}', got '{s.embedding_model}'",
                        )
                    )
                    continue

            endpoints = _endpoints(s)
            unresolved = sorted(endpoints - _resolve(session, endpoints))
            if unresolved:
                rejections.append(
                    Rejection(sid, f"unresolved refs: {unresolved}")
                )
                continue

            # 멤버십-grounding: CommunityReport(target이 community: 노드)는 모든
            # claim ref를 그 community의 멤버에 결박해야 한다.
            if s.target_id.startswith(_COMMUNITY_NS):
                claim_refs = {ref for claim in s.claims for ref in claim.source_refs}
                non_member = sorted(
                    claim_refs - _in_community(session, s.target_id, claim_refs)
                )
                if non_member:
                    rejections.append(
                        Rejection(sid, f"non-member refs (not in target community): {non_member}")
                    )
                    continue

            _write(session, sid, s, endpoints, _committed_at(session, s.target_id))
            loaded += 1
            if s.embedding is not None:
                embedded += 1

        indexed = _indexed_count(session)

    return SummaryLoadResult(
        intended=len(summaries),
        loaded=loaded,
        rejected=len(rejections),
        rejections=tuple(rejections),
        embedded=embedded,
        indexed=indexed,
    )
