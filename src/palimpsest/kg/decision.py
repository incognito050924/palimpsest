"""외부에서 생성된 DesignDecision을 KG에 적재한다(provider-free).

palimpsest는 LLM을 전혀 호출하지 않는다. DesignDecision은 외부 생성기가 내놓은
결정("이것은 설계 결정이다")으로, 근거결박되고 멱등(idempotent)한 적재를 위해
:func:`load_design_decisions`에 넘겨진다. Risk inferred 층과 마찬가지로, 두 개의
표식으로 결정론적 구조층과 분리(SEPARATE)를 유지한다:

  * 노드 라벨 ``DesignDecision`` (코드 라벨은 절대 아님), 그리고
  * 모든 DECIDES / SUPERSEDES / ADDRESSES_RISK 엣지의 ``edge_kind = "inferred"``
    (결정론적 엣지는 ``"deterministic"``) — 스키마가 강제하는 no-laundering 분리.

근거결박(entity-atomic). DesignDecision은 1개 이상의 ``DECIDES`` 타깃을 가져야 하고,
모든(EVERY) 엣지 타깃(DECIDES / SUPERSEDES / ADDRESSES_RISK)은 올바른 라벨의 실제
그래프 노드로 resolve돼야 한다: ``SUPERSEDES`` 타깃은 ``DesignDecision``이어야 하고
``ADDRESSES_RISK`` 타깃은 ``Risk``여야 한다(``DECIDES`` 타깃은 아무 기존 노드나 될 수
있다 — 보통 코드, 또는 다른 결정). DECIDES가 0개이거나, resolve되지 않거나 라벨이
틀린 타깃이 하나라도 있는 결정은 이유와 함께 REJECT한다 — 떠도는(floating) 결정
노드도, 부분 적재도 없다. 결정 하나를 거절해도 나머지는 멈추지 않는다. 결정론적
``_REL_MERGE`` writer는 일부러 재사용하지 않는다: 그것은 ``edge_kind='deterministic'``를
찍고(이 inferred 층을 laundering하게 된다), 그 ``MATCH..MATCH..MERGE``는 resolve되지
않는 endpoint에서 조용히 no-op이 된다; 여기서는 모든 타깃을 미리 resolve하고 불일치는
거절한다.

멱등(idempotence). 결정 id는 결정론적이고 네임스페이스로 격리돼 있으며
(``decision:<hash>`` — 코드 ``qualified_name``과 절대 충돌할 수 없다), 모든 쓰기가
MERGE-on-id라서 같은 payload를 다시 적재해도 아무것도 바뀌지 않는다.

신선도(freshness). ``code_bound_at``은 결정 대상 CODE 노드의 ``committed_at``에
결박된다(신선도는 생성기의 벽시계가 아니라 코드를 따른다). ``created_at``은 payload에
실려 온 외부 생성 시각이다.

범위 주석(이 슬라이스): 엣지 타깃은 LIVE 그래프에 대해서만 resolve한다. 같은 배치 내
엔티티 resolve(같은(THE SAME) 배치 호출에서 앞서 적재된 DesignDecision을 가리키는
SUPERSEDES)는 여기서는 범위 밖이다 — 유예된 정련.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from palimpsest.ir import (
    ADDRESSES_RISK,
    DECIDES,
    DESIGN_DECISION,
    EDGE_KIND_INFERRED,
    RISK,
    SUPERSEDES,
    DesignDecision,
)

# DesignDecision id의 네임스페이스 프리픽스 — 이 프리픽스를 가진 DECIDES 타깃은
# (코드 노드가 아니라) 다른 결정이므로, 코드 ``committed_at``을 갖지 않는다.
_DECISION_NS = "decision:"


@dataclass(frozen=True)
class DesignDecisionRejection:
    """거절된 DesignDecision과 그 이유 — 삼키지 않고 드러낸다."""

    decision_id: str
    reason: str


@dataclass(frozen=True)
class DesignDecisionLoadResult:
    """적재 배치의 결과: 집계 수치 + 명시적 거절 이유."""

    intended: int
    loaded: int
    rejected: int
    rejections: tuple[DesignDecisionRejection, ...] = ()


def decision_id(title: str, source_commit: str, targets) -> str:
    """결정론적이고 네임스페이스로 격리된 DesignDecision id.

    rebuild-stable한 키 위에서 만든다 — 정규화된 ``title``, ``source_commit``,
    그리고 모든 엣지 타깃(DECIDES + SUPERSEDES + ADDRESSES_RISK)의 SORTED된 합집합을
    NUL로 이어붙인 것 — 이라서 id는 타깃 순서에 무관하다
    (:func:`palimpsest.kg.risk.risk_id`와 동일). ``decision:`` 프리픽스는 id가 코드
    ``qualified_name``과 절대 같아질 수 없음을 보장하므로, 결정은 코드 노드를 절대
    가리지(shadow) 않는다; hash가 재적재를 멱등하게 만든다.
    """
    raw = "\x00".join([title.strip(), source_commit, *sorted(targets)])
    return "decision:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# 라벨("DesignDecision")과 rel type들은 쿼리 텍스트에 박아 넣은 닫힌 상수다; 결정의
# 모든 DATA(id, title, targets, provenance)는 ``$params``로 실려 들어오므로 적대적인
# title 텍스트는 무력하다.
_DECISION_MERGE = """
MERGE (d:DesignDecision {id: $id})
ON CREATE SET d.valid_from = $created_at, d.valid_to = null
SET d.title           = $title,
    d.decides         = $decides,
    d.supersedes      = $supersedes,
    d.addresses_risks = $addresses_risks,
    d.generator       = $generator,
    d.model           = $model,
    d.source_commit   = $source_commit,
    d.created_at      = $created_at,
    d.code_bound_at   = $code_bound_at,
    d.confidence      = $confidence,
    d.semantic_verdict = $semantic_verdict
"""

# endpoint는 위에서 미리 resolve된다(resolve되지 않거나 라벨이 틀리면 -> 그 결정
# 전체가 거절된다, 조용한 MATCH..MATCH..MERGE no-op은 없다). 따라서 여기의 모든 MERGE는
# materialize된다. ``{rel}``은 닫힌 상수다(DECIDES / SUPERSEDES / ADDRESSES_RISK),
# 데이터가 아니다 — kg/ingest.py의 ``_REL_MERGE`` 템플릿과 동일한 방식.
_EDGE_MERGE = """
MATCH (d:DesignDecision {{id: $id}})
UNWIND $targets AS tid
MATCH (t {{id: tid}})
MERGE (d)-[e:`{rel}`]->(t)
SET e.edge_kind     = $edge_kind,
    e.source_commit = $source_commit,
    e.created_at    = $created_at,
    e.code_bound_at = $code_bound_at,
    e.generator     = $generator,
    e.model         = $model,
    e.confidence    = $confidence
"""


# 결정-계보 신선도(2번째 축): 이전 결정을 SUPERSEDES하는 결정을 적재하면 이전 결정을
# INVALIDATE한다 — 그 ``valid_to``를 superseder의 ``created_at``으로 설정한다(현재가
# 아니게 된 시점). 이전 노드는 삭제되지 않고 PRESERVE된다(전이력 보존); "live"는 읽는
# 시점에 ``valid_to IS NULL``로 파생된다. 타깃은 미리 resolve되고 라벨 검사
# (DesignDecision)까지 마친 상태라, 여기의 모든 MATCH는 materialize된다.
_SUPERSEDE_INVALIDATE = """
UNWIND $targets AS tid
MATCH (t:DesignDecision {id: tid})
SET t.valid_to = $valid_to
"""


def _structural_reject_reason(d: DesignDecision):
    """결정이 겉보기에 잘못된 이유, 또는 형식이 온전하면 None
    (타깃의 근거결박은 live 그래프를 대상으로 별도로 검사한다)."""
    if not (d.generator and d.generator.strip()):
        return "missing generator"
    if not (d.model and d.model.strip()):
        return "missing model"
    if not d.decides:
        return "0-DECIDES decision (no grounding)"
    return None


def _resolve(session, ids, label=None) -> set:
    """``ids`` 중 실제 그래프 노드로 resolve되는 부분집합. ``label``이 주어지면
    (닫힌 상수, 데이터가 아님) 그(THAT) 라벨을 가진 노드만 인정된다 — 따라서
    라벨이 틀린 타깃은 resolve되지 않은 것(invalid)으로 취급한다."""
    if not ids:
        return set()
    match = f"MATCH (n:`{label}` {{id: id}})" if label else "MATCH (n {id: id})"
    rows = session.run(
        f"UNWIND $ids AS id {match} RETURN DISTINCT n.id AS id",
        ids=list(ids),
    )
    return {r["id"] for r in rows}


def _committed_at(session, node_id: str):
    rec = session.run(
        "MATCH (n {id: $id}) RETURN n.committed_at AS committed_at LIMIT 1",
        id=node_id,
    ).single()
    return rec["committed_at"] if rec else None


def _write(session, did, d, decides, supersedes, addresses_risks, code_bound_at):
    # Neo4j 속성은 map이 아니라 primitive/array라서, 외부 judge의 verdict는
    # JSON 문자열로 저장한다(recall에서 다시 파싱한다).
    semantic_verdict = (
        json.dumps(d.semantic_verdict, ensure_ascii=False)
        if d.semantic_verdict is not None
        else None
    )
    session.run(
        _DECISION_MERGE,
        id=did,
        title=d.title,
        decides=decides,
        supersedes=supersedes,
        addresses_risks=addresses_risks,
        generator=d.generator,
        model=d.model,
        source_commit=d.source_commit,
        created_at=d.created_at,
        code_bound_at=code_bound_at,
        confidence=d.confidence,
        semantic_verdict=semantic_verdict,
    )
    for rel, targets in (
        (DECIDES, decides),
        (SUPERSEDES, supersedes),
        (ADDRESSES_RISK, addresses_risks),
    ):
        if not targets:
            continue
        session.run(
            _EDGE_MERGE.format(rel=rel),
            id=did,
            targets=targets,
            edge_kind=EDGE_KIND_INFERRED,
            source_commit=d.source_commit,
            created_at=d.created_at,
            code_bound_at=code_bound_at,
            generator=d.generator,
            model=d.model,
            confidence=d.confidence,
        )
    # 결정-계보 신선도: superseded된 각 결정을 (삭제가 아니라) invalidate한다 —
    # 그 유효성은 이(THIS) 결정의 created_at에서 끝난다. 멱등한 SET; superseded된
    # 노드는 보존된다(전이력 보존), 그리고 그것을 재적재해도 valid_to를 절대 리셋하지
    # 않는다(그것은 노드 자신의 MERGE에서 ON-CREATE 전용이다).
    if supersedes:
        session.run(_SUPERSEDE_INVALIDATE, targets=supersedes, valid_to=d.created_at)


def load_design_decisions(driver, decisions) -> DesignDecisionLoadResult:
    """외부에서 생성된 DesignDecision을 inferred KG 층에 적재한다.

    각 결정을 검증한 뒤, 근거결박되면 resolve된 타깃들에 대해 DECIDES / SUPERSEDES /
    ADDRESSES_RISK 엣지(``edge_kind='inferred'``)를 가진 ``DesignDecision`` 노드로
    MERGE한다. 형식이 잘못됐거나, DECIDES가 0개이거나, resolve되지 않거나 라벨이 틀린
    타깃이 하나라도 있는 결정은 이유와 함께 REJECT한다(entity-atomic — 아무것도 쓰지
    않음); 나머지는 그대로 적재된다. intended/loaded/rejected 수치와 거절 이유를
    반환한다.
    """
    decisions = list(decisions)
    rejections: list[DesignDecisionRejection] = []
    loaded = 0
    with driver.session() as session:
        for d in decisions:
            decides = sorted(set(d.decides))
            supersedes = sorted(set(d.supersedes))
            addresses_risks = sorted(set(d.addresses_risks))
            all_targets = sorted(set(decides) | set(supersedes) | set(addresses_risks))
            did = decision_id(d.title, d.source_commit, all_targets)

            reason = _structural_reject_reason(d)
            if reason is not None:
                rejections.append(DesignDecisionRejection(did, reason))
                continue

            # 근거결박은 미리 resolve한다(조용한 MATCH..MERGE no-op 없음): resolve되지
            # 않는 DECIDES 타깃이나, 없거나 라벨이 틀린 SUPERSEDES/ADDRESSES_RISK 타깃이
            # 하나라도 있으면 결정 전체를 거절한다(entity-atomic) — 떠도는 노드도, 부분
            # 쓰기도 없다.
            unresolved = sorted(
                (set(decides) - _resolve(session, decides))
                | (set(supersedes) - _resolve(session, supersedes, DESIGN_DECISION))
                | (set(addresses_risks) - _resolve(session, addresses_risks, RISK))
            )
            if unresolved:
                rejections.append(
                    DesignDecisionRejection(did, f"unresolved edge targets: {unresolved}")
                )
                continue

            # 신선도는 결정론적으로 첫 번째인 DECIDES *코드* 타깃의 committed_at에
            # 앵커된다. ``decision:`` 네임스페이스를 가진 DECIDES 타깃은 다른
            # DesignDecision이므로(코드 committed_at 없음) 건너뛴다.
            # NOTE: Risk의 flags[0] 앵커와 마찬가지로, 이는 노드와 모든 엣지를 각 엣지
            # 고유의 것이 아니라 하나(ONE)의 타깃 commit에 결박한다 — 알려진 단순화
            # (single-code-target 결정에는 정확함). ADR change_condition
            # ("multi-target 신선도 재검토") 참조.
            code_decides = [t for t in decides if not t.startswith(_DECISION_NS)]
            code_bound_at = (
                _committed_at(session, code_decides[0]) if code_decides else None
            )
            _write(
                session, did, d, decides, supersedes, addresses_risks, code_bound_at
            )
            loaded += 1
    return DesignDecisionLoadResult(
        intended=len(decisions),
        loaded=loaded,
        rejected=len(rejections),
        rejections=tuple(rejections),
    )
