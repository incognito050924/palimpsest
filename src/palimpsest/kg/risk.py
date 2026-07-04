"""외부에서 생성된 Risk 판정을 KG에 적재한다(provider-free).

palimpsest는 LLM을 전혀 호출하지 않는다. Risk는 외부 생성기가 내놓은 판정("이 코드는
위험하다")으로, 근거결박되고 멱등(idempotent)한 적재를 위해 :func:`load_risks`에
넘겨진다. Summary inferred 층과 마찬가지로, 두 개의 표식으로 결정론적 구조층과
분리(SEPARATE)를 유지한다:

  * 노드 라벨 ``Risk`` (코드 라벨은 절대 아님), 그리고
  * 모든 ``RISKS`` 엣지의 ``edge_kind = "inferred"`` (결정론적 엣지는
    ``"deterministic"``) — 스키마가 강제하는 no-laundering 분리.

근거결박(entity-atomic). Risk는 1개 이상의 코드 노드 id를 flag해야 하고, 모든 flag는
실제 그래프 노드로 resolve돼야 한다. flag가 0개이거나 resolve되지 않는 flag가 하나라도
있는 Risk는 이유와 함께 REJECT한다 — 떠도는(floating) 판정 노드도, 부분 적재도 없다.
Risk 하나를 거절해도 나머지는 멈추지 않는다. 결정론적 ``_REL_MERGE`` writer는 일부러
재사용하지 않는다: 그것은 ``edge_kind='deterministic'``를 찍고(이 inferred 층을
laundering하게 된다), 그 ``MATCH..MATCH..MERGE``는 resolve되지 않는 endpoint에서
조용히 no-op이 된다; 여기서는 모든 flag를 미리 resolve하고 불일치는 거절한다.

멱등(idempotence). Risk id는 결정론적이고 네임스페이스로 격리돼 있으며
(``risk:<hash>`` — 코드 ``qualified_name``과 절대 충돌할 수 없다), 모든 쓰기가
MERGE-on-id라서 같은 payload를 다시 적재해도 아무것도 바뀌지 않는다.

신선도(freshness). ``code_bound_at``은 flag된 코드 노드의 ``committed_at``에 결박된다
(신선도는 생성기의 벽시계가 아니라 코드를 따른다). ``created_at``은 payload에 실려 온
외부 생성 시각이다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from palimpsest.ir import EDGE_KIND_INFERRED, Risk


@dataclass(frozen=True)
class RiskRejection:
    """거절된 Risk와 그 이유 — 삼키지 않고 드러낸다."""

    risk_id: str
    reason: str


@dataclass(frozen=True)
class RiskLoadResult:
    """적재 배치의 결과: 집계 수치 + 명시적 거절 이유."""

    intended: int
    loaded: int
    rejected: int
    rejections: tuple[RiskRejection, ...] = ()


def risk_id(title: str, source_commit: str, flags) -> str:
    """결정론적이고 네임스페이스로 격리된 Risk id.

    rebuild-stable한 키 위에서 만든다 — 정규화된 ``title``, ``source_commit``,
    그리고 SORTED된 ``flags``를 NUL로 이어붙인 것 — 이라서 id는 flag 순서에
    무관하다(:func:`palimpsest.kg.community.community_id`와 동일). ``risk:``
    프리픽스는 id가 코드 ``qualified_name``과 절대 같아질 수 없음을 보장하므로,
    Risk는 코드 노드를 절대 가리지(shadow) 않는다; hash가 재적재를 멱등하게 만든다.
    """
    raw = "\x00".join([title.strip(), source_commit, *sorted(flags)])
    return "risk:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# 라벨("Risk")과 rel type("RISKS")은 쿼리 텍스트에 박아 넣은 닫힌 상수다; Risk의
# 모든 DATA(id, title, flags, provenance)는 ``$params``로 실려 들어오므로 적대적인
# title 텍스트는 무력하다.
_RISK_MERGE = """
MERGE (r:Risk {id: $id})
SET r.title         = $title,
    r.flags         = $flags,
    r.generator     = $generator,
    r.model         = $model,
    r.source_commit = $source_commit,
    r.created_at    = $created_at,
    r.code_bound_at = $code_bound_at,
    r.confidence    = $confidence,
    r.semantic_verdict = $semantic_verdict
"""

_RISKS_MERGE = """
MATCH (r:Risk {id: $id})
UNWIND $flags AS fid
MATCH (t {id: fid})
MERGE (r)-[e:RISKS]->(t)
SET e.edge_kind     = $edge_kind,
    e.source_commit = $source_commit,
    e.created_at    = $created_at,
    e.code_bound_at = $code_bound_at,
    e.generator     = $generator,
    e.model         = $model,
    e.confidence    = $confidence
"""


def _structural_reject_reason(risk: Risk):
    """Risk가 겉보기에 잘못된 이유, 또는 형식이 온전하면 None
    (flag의 근거결박은 live 그래프를 대상으로 별도로 검사한다)."""
    if not (risk.generator and risk.generator.strip()):
        return "missing generator"
    if not (risk.model and risk.model.strip()):
        return "missing model"
    if not risk.flags:
        return "0-flag risk (no grounding)"
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


def _write(session, rid: str, risk: Risk, flags, code_bound_at) -> None:
    # Neo4j 속성은 map이 아니라 primitive/array라서, 외부 judge의 verdict는
    # JSON 문자열로 저장한다(recall에서 다시 파싱한다).
    semantic_verdict = (
        json.dumps(risk.semantic_verdict, ensure_ascii=False)
        if risk.semantic_verdict is not None
        else None
    )
    session.run(
        _RISK_MERGE,
        id=rid,
        title=risk.title,
        flags=flags,
        generator=risk.generator,
        model=risk.model,
        source_commit=risk.source_commit,
        created_at=risk.created_at,
        code_bound_at=code_bound_at,
        confidence=risk.confidence,
        semantic_verdict=semantic_verdict,
    )
    session.run(
        _RISKS_MERGE,
        id=rid,
        flags=flags,
        edge_kind=EDGE_KIND_INFERRED,
        source_commit=risk.source_commit,
        created_at=risk.created_at,
        code_bound_at=code_bound_at,
        generator=risk.generator,
        model=risk.model,
        confidence=risk.confidence,
    )


def load_risks(driver, risks) -> RiskLoadResult:
    """외부에서 생성된 Risk를 inferred KG 층에 적재한다."""
    risks = list(risks)
    rejections: list[RiskRejection] = []
    loaded = 0
    with driver.session() as session:
        for risk in risks:
            flags = sorted(risk.flags)
            rid = risk_id(risk.title, risk.source_commit, flags)

            reason = _structural_reject_reason(risk)
            if reason is not None:
                rejections.append(RiskRejection(rid, reason))
                continue

            # 근거결박은 미리 resolve한다(조용한 MATCH..MERGE no-op 없음): resolve되지
            # 않는 flag가 하나라도 있으면 Risk 전체를 거절하므로(entity-atomic),
            # 떠도는 판정 노드도 부분 쓰기도 없다.
            unresolved = sorted(set(flags) - _resolve(session, flags))
            if unresolved:
                rejections.append(
                    RiskRejection(rid, f"unresolved flag targets: {unresolved}")
                )
                continue

            # 신선도를 결정론적으로 첫 번째인 flag의 코드 노드에 앵커한다.
            # NOTE: 이는 노드와 모든 RISKS 엣지를 flags[0]의 commit에 결박한다
            # (각 flag 고유의 것이 아니라) — 알려진 단순화(Summary의 single-anchor와
            # 동일); single-flag인 동안은 무해하고, 아직 per-edge Risk 신선도를 읽는
            # 소비자가 없다(risk recall 채널 없음). ADR change_condition
            # ("multi-flag 신선도 재검토") 참조.
            _write(session, rid, risk, flags, _committed_at(session, flags[0]))
            loaded += 1
    return RiskLoadResult(
        intended=len(risks),
        loaded=loaded,
        rejected=len(rejections),
        rejections=tuple(rejections),
    )
