"""Knowledge Graph 위에서의 점진적·근거결박·조합적 회상.

설계 (ac-2 회상 + ac-3 정직성):

* **시드 해석(seed resolution).** 질의는 노드 ``id`` — 심볼 ``qualified_name`` 또는
  repo 상대 파일 경로(File 노드의 id는 곧 그 경로다)다. 둘 다 단일
  ``MATCH (n {id: $id})`` 하나로 해석한다. v1에는 자연어 파싱이 없다.

* **점진적·유계(bounded) 순회.** 결정론적 온톨로지(CALLS / DEPENDS_ON / CONTAINS /
  IMPORTS)를 너비 우선(BFS)으로, *무방향(undirected)*으로 순회한다. 그래서 Method는
  자신을 담은 Class(들어오는 CONTAINS)에도, 자신의 피호출자(나가는 CALLS)에도 닿는다.
  두 예산이 이를 유계로 만든다: ``depth``(최대 홉)와 ``limit``(최대 항목 수). 그래프
  전체를 적재하는 일은 결코 없다. 예산이 소진되면 아직 확장하지 않은 프런티어를
  ``expand_handle``로 반환하므로, 다음 홉은 :func:`expand`로 필요할 때 당겨 올 수 있다.

* **근거결박(grounding).** 회상된 모든 항목은 ``sources`` = ``source_commit`` +
  ``path`` + ``start_line`` / ``end_line``(KG가 찍어 둔 바로 그 출처)를 지닌다.
  최상위 ``sources`` 목록은 같은 근거결박을 *별도* 채널로 유지한 것이다.

* **오직 조합적(ac-3).** 조립은 순수한 그래프 순회 + dict 구성뿐이다. 이 경로 어디에도
  LLM / 생성 호출은 없다. 출력 필드는 분리를 유지한다 —
  ``{items, sources, summaries, gaps, confidence, expand_handle}``,
  결코 하나로 합쳐진 산문 "answer"가 아니다.

* **inferred 요약은 별도 채널로.** 외부에서 생성된 요약(inferred 층, 다른 곳에서 적재됨
  — palimpsest는 LLM을 호출하지 않는다)은 자신만의 ``summaries`` 채널에 드러나며,
  결코 ``items``에 병합되지 않는다. SUMMARIZES는 순회 가능한 관계가 아니다: 회상이
  걷는 것은 구조적 화이트리스트(CALLS / DEPENDS_ON / CONTAINS / IMPORTS)뿐이므로,
  Summary가 근거결박된 ``items``로 새어 드는 일은 결코 없다. 이 채널은 같은 depth/limit
  예산으로 유계다(이미 예산이 매겨진 회상 노드 id를 키로 쓴다). 각 항목은 자신의
  근거결박 refs, 결박된 커밋(``code_bound_at``), inferred ``edge_kind`` 표지를 지닌다.

* **간극(gap, ac-3 정직성).** 시드가 해석되지 않거나, *명시적으로 요청된* 관계가 시드에
  엣지를 하나도 갖지 않으면, 그것을 확신에 찬 빈 답으로 반환하는 대신 명시적 간극으로
  진술한다. 구조적 결합(coupling)을 결코 위험 / 품질 판단으로 제시하지 않는다.
"""

from __future__ import annotations

import json
import math
from datetime import datetime

from palimpsest.ir import (
    CALLS,
    DEPENDS_ON,
    CONTAINS,
    IMPORTS,
    MEMBER_OF,
    MODIFIES,
    INFERRED_RELATION_TYPES,
    EMBEDDING_DIM,
)
from palimpsest.kg.summary import VECTOR_INDEX_NAME

# 회상이 순회할 수 있는 관계(결정론적 구조 온톨로지).
DEFAULT_RELATIONS = (CALLS, DEPENDS_ON, CONTAINS, IMPORTS)

# DesignDecision id의 네임스페이스 프리픽스(kg/decision.py를 반영): 이 프리픽스를 가진
# DECIDES 대상은 코드가 아니라 또 다른 결정이므로, 코드 committed_at을 지니지 않는다.
_DECISION_NS = "decision:"

# 온톨로지 노드 라벨, 노드의 주(primary) kind를 고르는 순서대로.
_NODE_LABELS = ("Repo", "Package", "File", "Class", "Method", "Episode", "Community")

# 질의 id에는 라벨이 없지만, 유일성 CONSTRAINT는 (label, id) 단위이므로 서로 다른
# 라벨 아래 두 노드가 같은 id를 공유할 수 있다. LIMIT 1 전에 라벨로 정렬하여, Neo4j의
# 임의적인 스캔 순서 대신 해석이 결정론적이고 재빌드에도 안정적이게 한다(사전순으로
# 가장 작은 라벨이 이긴다).
_RESOLVE = (
    "MATCH (n {id: $id}) RETURN n, labels(n) AS labels "
    "ORDER BY head(labels(n)) LIMIT 1"
)

# 프런티어 id 집합에서 요청된 관계를 따라 무방향으로 한 홉. DISTINCT 행이며,
# limit 아래에서 항목 선택이 안정적이도록 결정론적 순서를 쓴다.
_NEIGHBORS = """
UNWIND $ids AS sid
MATCH (a {id: sid})-[r]-(b)
WHERE type(r) IN $rels
RETURN DISTINCT b.id AS id, type(r) AS relation, labels(b) AS labels,
       b.name AS name, b.qualified_name AS qualified_name,
       b.path AS path, b.start_line AS start_line, b.end_line AS end_line,
       b.source_commit AS source_commit, b.committed_at AS committed_at
ORDER BY id, relation
LIMIT $lim
"""

# 요청된 관계 중 실제로 시드에 엣지를 가진 것이 무엇인지.
_SEED_REL_TYPES = (
    "MATCH (a {id: $id})-[r]-() WHERE type(r) IN $rels "
    "RETURN DISTINCT type(r) AS relation"
)

# inferred 의미층을, 별도(SEPARATE) 채널로. 회상된 노드 id들(이미 depth/limit로 유계)에
# 대해, 각각에 붙은 Summary와 그것이 근거결박하는 코드 스팬을 당겨 온다. 구조상 유계다 —
# 결코 그래프 전체의 요약이 아니다. (summary, 근거결박 ref)당 한 행이며, ``edge_kind``가
# inferred 표지로 함께 실려 온다.
_SUMMARIES = """
UNWIND $ids AS anchor_id
MATCH (s:Summary)-[:SUMMARIZES]->({id: anchor_id})
WITH DISTINCT s
MATCH (s)-[r:SUMMARIZES]->(g)
RETURN s.id AS id, s.target_id AS target_id, s.claims AS claims,
       s.code_bound_at AS code_bound_at, s.semantic_verdict AS semantic_verdict,
       r.edge_kind AS edge_kind,
       g.id AS ref_id, g.source_commit AS source_commit, g.path AS path,
       g.start_line AS start_line, g.end_line AS end_line,
       g.committed_at AS committed_at
ORDER BY id, ref_id
LIMIT $lim
"""


def _kind(labels) -> str | None:
    labels = list(labels or ())
    for known in _NODE_LABELS:
        if known in labels:
            return known
    return labels[0] if labels else None


def _sources(rec) -> dict:
    return {
        "source_commit": rec.get("source_commit"),
        "path": rec.get("path"),
        "start_line": rec.get("start_line"),
        "end_line": rec.get("end_line"),
        "committed_at": rec.get("committed_at"),
    }


def _item(rec, relation, depth) -> dict:
    return {
        "id": rec.get("id"),
        "kind": _kind(rec.get("labels")),
        "name": rec.get("name"),
        "qualified_name": rec.get("qualified_name"),
        "relation": relation,   # 어떤 관계로 닿았는지(시드의 경우 None)
        "depth": depth,
        "sources": _sources(rec),
    }


def _is_grounded(item) -> bool:
    s = item["sources"]
    return bool(s["source_commit"] and s["path"] and s["start_line"] is not None)


def _confidence(items) -> float:
    """결정론적 근거결박 커버리지: 반환된 항목 중 구체적 코드(commit + file:line)로
    해석되는 비율. 조합적이며, 모델 점수가 아니다. 회상이 비면 -> 0.0."""
    if not items:
        return 0.0
    grounded = sum(1 for it in items if _is_grounded(it))
    return round(grounded / len(items), 3)


def _resolve(driver, query):
    with driver.session() as session:
        rec = session.run(_RESOLVE, id=query).single()
    if rec is None:
        return None
    node = rec["n"]
    data = dict(node)
    data["labels"] = list(rec["labels"])
    return data


def _neighbors(driver, ids, rels, limit):
    # 순회 화이트리스트: 오직 결정론적 구조 온톨로지만 걷는다. SUMMARIZES(inferred)는
    # — 호출자가 ``relations``에 넘기더라도 — 순회 불가하므로, Summary가 items 채널로
    # 새어 드는 일은 결코 없다. 그것은 오직 별도의 summaries 채널로만 드러난다.
    #
    # ``limit``은 서버측 행 한계(ORDER BY 뒤의 Cypher ``LIMIT``)이므로, 차수가 높은
    # 노드라도 이웃 집합 전체를 클라이언트로 스트리밍하는 일은 결코 없다.
    rels = [r for r in rels if r in DEFAULT_RELATIONS]
    with driver.session() as session:
        rows = session.run(_NEIGHBORS, ids=list(ids), rels=rels, lim=limit)
        return [r.data() for r in rows]


def _stale(code_bound_at, target_committed_at) -> bool:
    """#4 감지 전용(detect-only) 신선도 플래그. 요약이 결박된 이후 대상 코드 노드가 다시
    커밋되었으면 그 요약은 stale이다: 즉 현재 ``committed_at``이 요약의 ``code_bound_at``과
    다르다(적재 시점엔 구조상 같다 — kg/summary.py 참조). 둘 중 하나라도 없으면 신선도를
    판정할 수 없으므로, staleness를 주장하지 않는다(stale=False). 순수 비교 —
    LLM도, 재생성도 없다."""
    if code_bound_at is None or target_committed_at is None:
        return False
    return target_committed_at != code_bound_at


def _summary_channel(rows) -> list:
    """평탄한 (summary, 근거결박-ref) 행들을 별도의 summaries 채널로 묶는다: Summary당
    한 항목이며, 각각 자신의 근거결박 refs(:func:`_sources`를 통해 author는 생략),
    결박된 커밋(``code_bound_at``), inferred ``edge_kind`` 표지, ``stale`` 신선도
    플래그를 지닌다. 요약 텍스트는 여기 머문다 — 결코 items에 병합되지 않는다."""
    by_id: dict = {}
    for row in rows:
        entry = by_id.get(row["id"])
        if entry is None:
            entry = {
                "id": row["id"],
                "target_id": row["target_id"],
                "claims": [json.loads(c) for c in (row["claims"] or [])],
                "edge_kind": row["edge_kind"],      # inferred 표지, 엣지에서 온 것
                "code_bound_at": row["code_bound_at"],  # 결박된 커밋(신선도)
                # 외부 판정자(judge)의 verdict(주석 전용 플래그), 저장된 JSON에서 파싱;
                # 없으면 -> None(미검증으로 취급). palimpsest는 결코 판정하지 않는다 —
                # 외부 판정자가 적재한 것을 드러낼 뿐이다.
                "semantic_verdict": (
                    json.loads(row["semantic_verdict"])
                    if row.get("semantic_verdict")
                    else None
                ),
                "refs": [],
                # 대상 자신의 근거결박 행이 보이면(아래) 채워진다; 대상 ref는 항상
                # 존재한다(kg/summary.py가 대상을 근거결박한다).
                "stale": False,
            }
            by_id[row["id"]] = entry
        entry["refs"].append({"id": row["ref_id"], **_sources(row)})
        # 신선도 기준은 대상(TARGET) 노드의 현재 committed_at을 따른다.
        if row["ref_id"] == entry["target_id"]:
            entry["stale"] = _stale(entry["code_bound_at"], row["committed_at"])
    return list(by_id.values())


def _summaries(driver, items, limit) -> list:
    """회상된 ``items``(이미 유계)에 대한 inferred-summary 채널.

    ``limit``은 서버측 행 한계(ORDER BY 뒤의 Cypher ``LIMIT``)이므로, 요약이 많은
    노드라도 요약 집합 전체를 클라이언트로 스트리밍하는 일은 결코 없다."""
    if not items:
        return []
    ids = [it["id"] for it in items]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_SUMMARIES, ids=ids, lim=limit)]
    return _summary_channel(rows)


# inferred 설계-위험 채널(slice 2 "위험 표시"), 'summaries'를 반영한 각각 별도(SEPARATE)
# 채널. 회상된 노드 id들(이미 depth/limit로 유계)에 대해, 그중 하나를 FLAGS하는 각 Risk /
# 그중 하나를 DECIDES하는 각 DesignDecision과, 그것이 근거결박하는 코드 스팬을 당겨 온다.
# 구조상 유계다. (entity, 근거결박 ref)당 한 행이며, ``edge_kind``가 inferred 표지로 함께
# 실려 온다. RISKS / DECIDES는 결코 순회 가능한 관계가 아니므로, Risk / DesignDecision이
# items로 새어 드는 일은 결코 없다 — 그것은 오직 이 채널들로만 드러난다.
_RISKS_CHANNEL = """
UNWIND $ids AS anchor_id
MATCH (r:Risk)-[:RISKS]->({id: anchor_id})
WITH DISTINCT r
MATCH (r)-[e:RISKS]->(g)
RETURN r.id AS id, r.title AS title, r.flags AS anchors,
       r.code_bound_at AS code_bound_at, r.semantic_verdict AS semantic_verdict,
       r.confidence AS confidence, e.edge_kind AS edge_kind,
       g.id AS ref_id, g.source_commit AS source_commit, g.path AS path,
       g.start_line AS start_line, g.end_line AS end_line,
       g.committed_at AS committed_at
ORDER BY id, ref_id
LIMIT $lim
"""

_DECISIONS_CHANNEL = """
UNWIND $ids AS anchor_id
MATCH (d:DesignDecision)-[:DECIDES]->({id: anchor_id})
WITH DISTINCT d
MATCH (d)-[e:DECIDES]->(g)
RETURN d.id AS id, d.title AS title, d.decides AS anchors,
       d.code_bound_at AS code_bound_at, d.semantic_verdict AS semantic_verdict,
       d.confidence AS confidence, e.edge_kind AS edge_kind,
       d.valid_from AS valid_from, d.valid_to AS valid_to,
       g.id AS ref_id, g.source_commit AS source_commit, g.path AS path,
       g.start_line AS start_line, g.end_line AS end_line,
       g.committed_at AS committed_at
ORDER BY id, ref_id
LIMIT $lim
"""


def _bound_anchor(anchors) -> str | None:
    """Risk/Decision의 ``code_bound_at``이 적재 시점에 결박된 대상 코드 — 신선도 앵커.
    로더를 반영한다: Risk는 ``sorted(flags)[0]``에 결박되고, 결정은 자신의 첫 *코드*
    DECIDES 대상에 결박된다(``decision:`` 네임스페이스 대상은 코드가 아니라 다른 결정이므로
    건너뛴다). ``anchors``는 이미 정렬되어 도착한다(로더가 저장 전에 정렬한다)."""
    for a in (anchors or []):
        if not a.startswith(_DECISION_NS):
            return a
    return None


def _entity_channel(rows) -> list:
    """평탄한 (entity, 근거결박-ref) 행들을 inferred 설계-위험 채널로 묶는다:
    Risk/DesignDecision당 한 항목이며, 각각 자신의 근거결박 refs(author 생략), inferred
    ``edge_kind`` 표지, 결박된 커밋(``code_bound_at``), 외부 판정자의 ``semantic_verdict``
    (파싱됨), ``stale`` 신선도 플래그(summaries 채널을 반영해 신선도-앵커 ref에서 설정)를
    지닌다. title은 여기 머문다 — 결코 items에 병합되지 않는다."""
    by_id: dict = {}
    for row in rows:
        entry = by_id.get(row["id"])
        if entry is None:
            entry = {
                "id": row["id"],
                "title": row["title"],
                "edge_kind": row["edge_kind"],        # inferred 표지, 엣지에서 온 것
                "code_bound_at": row["code_bound_at"],  # 결박된 커밋(신선도)
                "confidence": row["confidence"],
                # 외부 판정자의 verdict(주석 전용), 저장된 JSON에서 파싱; 없으면 -> None.
                # palimpsest는 결코 판정하지 않는다 — 이것을 드러낼 뿐이다.
                "semantic_verdict": (
                    json.loads(row["semantic_verdict"])
                    if row.get("semantic_verdict")
                    else None
                ),
                "refs": [],
                "stale": False,
                "_anchor": _bound_anchor(row.get("anchors")),
            }
            # 결정-계보 신선도(2번째 축) — decisions 채널에서만(이 질의는 valid_to를
            # 반환하지만 risks 질의는 아니다). 대체(superseded)되어도 항목은 여전히
            # 드러난다(전이력 보존); ``live``는 현행성(current-currency) 판단으로,
            # valid_to IS NULL로 유도된다.
            if "valid_to" in row:
                entry["valid_from"] = row.get("valid_from")
                entry["valid_to"] = row.get("valid_to")
                entry["live"] = row.get("valid_to") is None
            by_id[row["id"]] = entry
        entry["refs"].append({"id": row["ref_id"], **_sources(row)})
        # 신선도는 결박 앵커의 현재 committed_at을 따른다(로더 참조).
        if row["ref_id"] == entry["_anchor"]:
            entry["stale"] = _stale(entry["code_bound_at"], row["committed_at"])
    out = list(by_id.values())
    for entry in out:
        entry.pop("_anchor", None)
    return out


def _risks(driver, items, limit) -> list:
    """회상된 ``items``(이미 유계)에 대한 inferred Risk 채널.

    ``limit``은 서버측 행 한계(ORDER BY 뒤의 Cypher ``LIMIT``)다."""
    if not items:
        return []
    ids = [it["id"] for it in items]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_RISKS_CHANNEL, ids=ids, lim=limit)]
    return _entity_channel(rows)


def _decisions(driver, items, limit) -> list:
    """회상된 ``items``(유계)에 대한 inferred DesignDecision 채널."""
    if not items:
        return []
    ids = [it["id"] for it in items]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_DECISIONS_CHANNEL, ids=ids, lim=limit)]
    return _entity_channel(rows)


# inferred-관계(RELATION) 채널 — 회상된 노드에 닿는 순수 inferred 엣지(CAUSALLY_RELATES /
# RELATES_TO / CONFLICTS_WITH). 엣지당 한 항목(양 끝점이 모두 회상된 경우 ``WITH DISTINCT
# e``가 중복을 제거한다). 이 관계 타입들은 의도적으로 DEFAULT_RELATIONS에 없으므로, items
# 순회에 결코 들어가지 않는다; 오직 여기서만 드러난다. ``$rel_types``는 닫힌 inferred 집합.
_RELATIONS_CHANNEL = """
UNWIND $ids AS anchor_id
MATCH (a {id: anchor_id})-[e]-()
WHERE type(e) IN $rel_types
WITH DISTINCT e
RETURN type(e) AS rel_type,
       startNode(e).id AS source_id, endNode(e).id AS target_id,
       e.edge_kind AS edge_kind, e.source_commit AS source_commit,
       e.created_at AS created_at, e.code_bound_at AS code_bound_at,
       e.confidence AS confidence, e.semantic_verdict AS semantic_verdict
ORDER BY rel_type, source_id, target_id
LIMIT $lim
"""


def _relation_entry(row) -> dict:
    return {
        "rel_type": row["rel_type"],
        "source_id": row["source_id"],
        "target_id": row["target_id"],
        "edge_kind": row["edge_kind"],           # inferred 표지, 엣지에서 온 것
        "code_bound_at": row["code_bound_at"],
        "confidence": row["confidence"],
        # 외부 판정자의 verdict(주석 전용), 저장된 JSON에서 파싱; 없으면 -> None.
        # palimpsest는 결코 판정하지 않는다 — 적재된 것을 드러낼 뿐이다.
        "semantic_verdict": (
            json.loads(row["semantic_verdict"]) if row.get("semantic_verdict") else None
        ),
        "source_commit": row["source_commit"],
        "created_at": row["created_at"],
    }


def _relations(driver, items, limit) -> list:
    """회상된 ``items``(이미 유계)에 대한 inferred-관계 채널.

    ``limit``은 서버측 행 한계(ORDER BY 뒤의 Cypher ``LIMIT``)다."""
    if not items:
        return []
    ids = [it["id"] for it in items]
    with driver.session() as session:
        rows = [
            r.data()
            for r in session.run(
                _RELATIONS_CHANNEL,
                ids=ids,
                rel_types=list(INFERRED_RELATION_TYPES),
                lim=limit,
            )
        ]
    return [_relation_entry(row) for row in rows]


def _seed_relation_gaps(driver, query, relations):
    """오직 명시적으로 요청된 관계에 대해서만: 시드에 엣지가 하나도 없는 관계는
    정직한 간극이다(확신에 찬 빈 답은 부정직할 것이다)."""
    with driver.session() as session:
        present = {
            r["relation"]
            for r in session.run(_SEED_REL_TYPES, id=query, rels=list(relations))
        }
    return [
        f"requested relation {rel} has no edges on seed '{query}'"
        for rel in relations
        if rel not in present
    ]


def _hop(driver, frontier, visited, relations, depth, budget):
    """무방향 BFS 한 홉. 최대 ``budget``개의 새 항목을 낸다(결정론적 순서).
    (items, emitted_ids, truncated)를 반환한다."""
    items, emitted = [], []
    truncated = False
    # 서버측 한계: (최대 ``len(visited)``개의) 이미 본 행을 건너뛴 뒤, 아직 방문하지 않은
    # ``budget``개가 남아야 하고, 절단(truncation)을 감지할 하나가 더 필요하다 — 따라서
    # 정렬된 처음 ``budget + |visited| + 1``개 행이면 증명 가능하게 충분하며, 집합 전체를
    # 읽는 것과 동일한 items/emitted/truncated를 낸다.
    read_limit = budget + len(visited) + 1
    for rec in _neighbors(driver, frontier, relations, read_limit):
        nid = rec["id"]
        if nid in visited:
            continue
        if len(items) >= budget:
            truncated = True
            break
        visited.add(nid)
        items.append(_item(rec, rec["relation"], depth))
        emitted.append(nid)
    return items, emitted, truncated


def _result(items, gaps, handle, summaries, risks=None, decisions=None, relations=None):
    return {
        "items": items,
        # 별도 근거결박 채널(id를 키로), items를 반영 — 결코 병합되지 않는다.
        "sources": [{"id": it["id"], **it["sources"]} for it in items],
        # 별도 inferred-summary 채널 — 결코 items에 병합되지 않는다.
        "summaries": summaries,
        # 별도 inferred 설계-위험 채널(slice 2 "위험 표시") — 회상된 코드를 flag하는
        # Risk / decide하는 DesignDecision이며, 결코 items에 병합되지 않는다. 감지 흐름을
        # 돌리지 않는 진입점에서는 빈 값이다.
        "risks": risks or [],
        "decisions": decisions or [],
        # 별도 inferred-관계 채널 — 회상된 노드에 닿는 CAUSALLY_RELATES / RELATES_TO /
        # CONFLICTS_WITH 엣지이며, 결코 items에 병합되지 않는다.
        "relations": relations or [],
        "gaps": gaps,
        "confidence": _confidence(items),
        "expand_handle": handle,
    }


def _handle(frontier, visited, next_depth, relations):
    """다음 홉을 위한 pull-handle, 프런티어가 비었으면 None."""
    if not frontier:
        return None
    return {
        "frontier": list(frontier),
        "visited": list(visited),
        "depth": next_depth,
        "relations": list(relations),
    }


def recall(driver, query, depth=1, limit=25, relations=None):
    """시드 노드에서 출발하는 점진적·근거결박·조합적 회상.

    ``query``는 노드 id(심볼 ``qualified_name`` 또는 repo 상대 경로)다.
    CALLS / DEPENDS_ON / CONTAINS / IMPORTS를 최대 ``depth`` 홉까지 순회하며,
    최대 ``limit``개 항목을 반환한다.
    ``{items, sources, summaries, gaps, confidence, expand_handle}``를 반환한다 —
    ``summaries`` 채널은 회상된 노드에 근거결박된 inferred 요약을 담으며,
    ``items``와 분리해서 유지한다.
    """
    # 간극은 호출자가 관계 집합을 *명시적으로* 좁힐 때만 관계별로 제기된다; 기본값(네 가지
    # 전부)은 고립되거나 해석되지 않은 시드에 대해서만 간극을 보고한다.
    explicit_relations = relations is not None
    relations = tuple(relations) if relations is not None else DEFAULT_RELATIONS

    seed = _resolve(driver, query)
    if seed is None:
        gap = f"seed '{query}' did not resolve to any node in the graph"
        return _result([], [gap], None, [])

    seed_item = _item(seed, None, 0)
    items = [seed_item]
    visited = {seed["id"]}
    frontier = [seed["id"]]
    cur_depth = 0
    truncated = False

    while frontier and cur_depth < depth and len(items) < limit:
        cur_depth += 1
        new_items, emitted, truncated = _hop(
            driver, frontier, visited, relations, cur_depth, limit - len(items)
        )
        items.extend(new_items)
        if truncated:
            # 레벨 중간에 예산 소진: 확장 중이던 레벨에서 재개하여, 건너뛴 형제들
            # (``frontier``의 방문하지 않은 이웃)이 다음에 당길 홉이 되게 한다.
            break
        frontier = emitted

    # 더 당길 게 있나? limit이 한 레벨을 중간에 끊었거나, depth 한계에 도달했는데 바깥
    # 프런티어에 아직 방문하지 않은 이웃이 남아 있는 경우다.
    if truncated:
        handle = _handle(frontier, visited, cur_depth, relations)
    elif frontier and _neighbors_beyond(driver, frontier, visited, relations):
        handle = _handle(frontier, visited, cur_depth + 1, relations)
    else:
        handle = None

    gaps = _seed_relation_gaps(driver, query, relations) if explicit_relations else []
    return _result(
        items, gaps, handle,
        _summaries(driver, items, limit),
        _risks(driver, items, limit),
        _decisions(driver, items, limit),
        _relations(driver, items, limit),
    )


def _neighbors_beyond(driver, frontier, visited, relations) -> bool:
    """프런티어에 아직 방문하지 않은 이웃이 하나라도 있으면 True.

    존재 확인만 한다: 첫 미방문 행은 정렬된 처음 ``|visited| + 1``개 행 안에 있으므로,
    그 서버측 한계로 충분하다 — 이웃 집합 전체를 스트리밍할 필요가 없다."""
    for rec in _neighbors(driver, frontier, relations, len(visited) + 1):
        if rec["id"] not in visited:
            return True
    return False


# Community 멤버를, 별도(SEPARATE) 진입점으로(결코 순회 가능한 관계가 아니다: MEMBER_OF는
# 의도적으로 DEFAULT_RELATIONS에 없으므로, Community가 일반 items 순회로 새어 드는 일은
# 결코 없다 — summaries 채널의 분리를 반영한다). 멤버 Class당 한 행이며, 근거결박된
# (commit + file:line) 결정론적 순서다.
_COMMUNITY_MEMBERS = """
MATCH (c:Class)-[:MEMBER_OF]->(:Community {id: $id})
RETURN c.id AS id, labels(c) AS labels, c.name AS name,
       c.qualified_name AS qualified_name,
       c.path AS path, c.start_line AS start_line, c.end_line AS end_line,
       c.source_commit AS source_commit, c.committed_at AS committed_at
ORDER BY id
LIMIT $lim
"""


def recall_community(driver, community_id, limit=25):
    """Community의 멤버 Class들을, 별도(SEPARATE) 진입점으로 회상한다.

    ``community_id``는 Community 노드 id다. 같은
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` 형태를 반환한다 —
    멤버 Class들이 근거결박된 ``items``(:func:`_sources`를 통해 commit + file:line,
    author 생략)이며, ``limit``으로 유계다. 이는 오직 조합적(단일 MEMBER_OF 질의 + dict
    구성)이다 — LLM 없음, 그리고 MEMBER_OF를 일반 순회 관계로 걷는 일은 결코 없다.
    """
    with driver.session() as session:
        if session.run(_RESOLVE, id=community_id).single() is None:
            gap = f"community '{community_id}' did not resolve to any node in the graph"
            return _result([], [gap], None, [])
        rows = [r.data() for r in session.run(_COMMUNITY_MEMBERS, id=community_id, lim=limit)]
    items = [_item(rec, MEMBER_OF, 1) for rec in rows]
    # 구조적 결합(community) 위에 inferred 표시: 멤버 class들에 붙은 Summary(멤버 Class에
    # 근거결박되는 community 자신의 CommunityReport 포함), Risk, DesignDecision이 각자의
    # 별도 채널로 드러난다 — 멤버 id를 키로 한, 메인 회상과 동일한 역방향 조회다. 결코
    # items에 병합되지 않는다.
    return _result(
        items, [], None,
        _summaries(driver, items, limit),
        _risks(driver, items, limit),
        _decisions(driver, items, limit),
        _relations(driver, items, limit),
    )


# inferred 엔티티(Risk / DesignDecision)를, 별도(SEPARATE) 진입점으로 — MEMBER_OF와
# SUMMARIZES가 받는 것과 같은 분리다: 이들의 inferred 엣지(RISKS / DECIDES / SUPERSEDES /
# ADDRESSES_RISK)는 의도적으로 DEFAULT_RELATIONS에 없으므로, inferred 엔티티가 일반 items
# 순회로 새어 드는 일은 결코 없다; 오직 아래의 전용 회상으로만 닿을 수 있다.

# Risk가 flag하는 코드 노드(RISKS 대상)당 한 행, 근거결박, 결정론적 순서.
_RISK_FLAGS = """
MATCH (:Risk {id: $id})-[:RISKS]->(t)
RETURN t.id AS id, labels(t) AS labels, t.name AS name,
       t.qualified_name AS qualified_name,
       t.path AS path, t.start_line AS start_line, t.end_line AS end_line,
       t.source_commit AS source_commit, t.committed_at AS committed_at
ORDER BY id
LIMIT $lim
"""

_RISK_EXISTS = "MATCH (r:Risk {id: $id}) RETURN r LIMIT 1"


def recall_risk(driver, risk_id, limit=25):
    """Risk가 flag하는 코드를, 별도(SEPARATE) 진입점으로 회상한다.

    ``risk_id``는 ``Risk`` 노드 id(``risk:<hash>``)다. 같은
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` 형태를 반환한다 —
    flag된 코드 노드들이 근거결박된 ``items``(inferred ``RISKS`` 엣지로 닿으며,
    :func:`_sources`를 통해 commit + file:line, author 생략)이며, ``limit``으로 유계다.
    오직 조합적(단일 RISKS 질의 + dict 구성)이다 — LLM 없음, 그리고 RISKS를 일반 순회
    관계로 걷는 일은 결코 없다. 해석되지 않은 id는 확신에 찬 빈 답이 아니라 명시적 간극이다.
    """
    with driver.session() as session:
        if session.run(_RISK_EXISTS, id=risk_id).single() is None:
            gap = f"risk '{risk_id}' did not resolve to any Risk node in the graph"
            return _result([], [gap], None, [])
        rows = [r.data() for r in session.run(_RISK_FLAGS, id=risk_id, lim=limit)]
    items = [_item(rec, "RISKS", 1) for rec in rows]
    return _result(items, [], None, [])


# DesignDecision 대상당 한 행, 각각 자신의 엣지 타입을 ``relation``으로 지닌다
# (DECIDES code|decision / SUPERSEDES decision / ADDRESSES_RISK risk). 이 세 관계 타입은
# 질의에 박아 넣은 닫힌 화이트리스트다 — 결정의 나가는 엣지는 구조상 전부 inferred이며,
# 이들을 명시함으로써 회상을 의도한 엣지로만 한정한다. 결정론적 순서(id, relation).
_DECISION_TARGETS = """
MATCH (:DesignDecision {id: $id})-[e:DECIDES|SUPERSEDES|ADDRESSES_RISK]->(t)
RETURN t.id AS id, type(e) AS relation, labels(t) AS labels, t.name AS name,
       t.qualified_name AS qualified_name,
       t.path AS path, t.start_line AS start_line, t.end_line AS end_line,
       t.source_commit AS source_commit, t.committed_at AS committed_at
ORDER BY id, relation
LIMIT $lim
"""

_DECISION_EXISTS = "MATCH (d:DesignDecision {id: $id}) RETURN d LIMIT 1"


def recall_decision(driver, decision_id, limit=25):
    """DesignDecision이 무엇에 결부하는지를, 별도(SEPARATE) 진입점으로 회상한다.

    ``decision_id``는 ``DesignDecision`` 노드 id(``decision:<hash>``)다. 같은
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` 형태를 반환한다 —
    결정의 대상들이 ``items``이며, 각각 자신의 엣지 타입으로 라벨된다
    (``relation`` = DECIDES / SUPERSEDES / ADDRESSES_RISK), ``limit``으로 유계다.
    DECIDES *코드* 대상은 근거결박된다(:func:`_sources`를 통해 commit + file:line);
    SUPERSEDES / ADDRESSES_RISK 대상은 inferred 엔티티(코드 스팬 없음)이므로,
    ``confidence``(근거결박 커버리지)가 그 혼합을 정직하게 반영한다. 오직 조합적(단일
    질의 + dict 구성)이다 — LLM 없음, 그리고 이 inferred 엣지들을 일반 순회 관계로 걷는
    일은 결코 없다. 해석되지 않은 id는 확신에 찬 빈 답이 아니라 명시적 간극이다.
    """
    with driver.session() as session:
        if session.run(_DECISION_EXISTS, id=decision_id).single() is None:
            gap = f"decision '{decision_id}' did not resolve to any DesignDecision node in the graph"
            return _result([], [gap], None, [])
        rows = [r.data() for r in session.run(_DECISION_TARGETS, id=decision_id, lim=limit)]
    items = [_item(rec, rec["relation"], 1) for rec in rows]
    return _result(items, [], None, [])


def expand(driver, handle, limit=25):
    """:func:`recall`이 반환한 ``expand_handle``에서 다음 홉을 당겨 온다.

    조합적이고 필요할 때(on-demand) 이어가는 방식: 핸들의 프런티어에서 BFS를 한 홉 더,
    이미 본 노드는 건너뛰고, ``limit``으로 유계로. 같은
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` 형태를 반환한다.
    """
    if not handle or not handle.get("frontier"):
        return _result([], ["no frontier to expand"], None, [])

    relations = tuple(handle.get("relations") or DEFAULT_RELATIONS)
    visited = set(handle["visited"])
    frontier = list(handle["frontier"])
    depth = handle["depth"]

    new_items, emitted, truncated = _hop(
        driver, frontier, visited, relations, depth, limit
    )

    if truncated:
        next_handle = _handle(frontier, visited, depth, relations)
    elif emitted and _neighbors_beyond(driver, emitted, visited, relations):
        next_handle = _handle(emitted, visited, depth + 1, relations)
    else:
        next_handle = None

    return _result(
        new_items, [], next_handle,
        _summaries(driver, new_items, limit),
        _risks(driver, new_items, limit),
        _decisions(driver, new_items, limit),
        _relations(driver, new_items, limit),
    )


# 한 심볼의 branch-scoped 피어(peer): ``qualified_name``이 그 심볼과 같으면서 ``branch``가
# 호출자의 집합에 속하는 모든 노드. 그룹핑은 저장된 ``qualified_name`` 속성으로 한다
# (branch-scoped id는 서로 다르므로 id로는 그룹핑할 수 없다). ``branch IN $branches``는
# 맨-id 평면(branch=null)과 지정되지 않은 모든 branch를 구조상 배제한다(ac-6). author(%ae)는
# 결코 선택되지 않는다 — author 생략은 projection으로 성립한다. 안정적 타이브레이크를 위한
# 결정론적 중립 순서(branch); 실제 정렬 키는 클라이언트측에서 계산한다(UTC 순간).
_BRANCH_PEERS = """
MATCH (n) WHERE n.qualified_name = $symbol AND n.branch IN $branches
RETURN n.id AS id, n.branch AS branch, n.qualified_name AS qualified_name,
       labels(n) AS labels, n.name AS name,
       n.path AS path, n.start_line AS start_line, n.end_line AS end_line,
       n.source_commit AS source_commit, n.committed_at AS committed_at
ORDER BY n.branch
"""


def _utc_instant(committed_at):
    """원시 ``committed_at``(%cI, tz 오프셋을 가진 ISO-8601)의 절대 UTC 순간. py3.12
    ``fromisoformat``이 오프셋을 파싱하여 -> 절대 순간으로 비교되는 tz-aware datetime을
    낸다. 없거나 파싱 불가 -> None(맨 뒤로 정렬되며, 결코 freshest가 아니다). tz-NAIVE
    값(오프셋 없음)도 파싱 불가로 취급한다: tz-aware 피어들과 예외 없이 비교할 수 없고,
    %cI 계약은 항상 오프셋을 내보내므로, naive 값은 순위 매길 수 있는 순간이 아니라
    계약 위반(off-contract)이다. 순수 비교 — LLM도, 저장 값 변경도 없다."""
    if not committed_at:
        return None
    try:
        instant = datetime.fromisoformat(committed_at)
    except (ValueError, TypeError):
        return None
    if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
        return None  # tz-naive: 계약 위반, 파싱 불가처럼 맨 뒤로 정렬
    return instant


def _peer_semantic(driver, peer_id, limit):
    """한(ONE) 피어에 결박된 inferred 의미층을, 메인 회상 채널과 똑같이 읽는다(각 헬퍼가
    저장된 verdict를 json.loads; 없으면 -> None). 표시 전용(DISPLAY-ONLY): 신선도가 피어를
    정렬하고, 이것은 그 곁에 나란히 보여지며, 병합되지 않는다. palimpsest는 이 중 아무것도
    생성하지 않는다 — 외부 판정자가 적재한 것을 드러낼 뿐이다."""
    items = [{"id": peer_id}]
    return {
        "summaries": _summaries(driver, items, limit),
        "risks": _risks(driver, items, limit),
        "decisions": _decisions(driver, items, limit),
        "relations": _relations(driver, items, limit),
    }


def _ranked_peers(rows):
    """피어를 UTC 순간이 가장 최신인 것부터 정렬한다. 안정성 전용 중립 타이브레이크
    (branch 이름)를 쓰되 — branch 이름은 아무 우선순위도 지니지 않는다. 파싱 불가/없는
    순간을 가진 피어는 맨 뒤로 정렬된다. 최대 순간에 있는 모든 피어에 ``freshest:true``
    (동률이면 공동 freshest; 단일 승자를 지어내지 않는다)."""
    parseable, unparseable = [], []
    for r in rows:
        r["_instant"] = _utc_instant(r.get("committed_at"))
        (parseable if r["_instant"] is not None else unparseable).append(r)
    # 안정적인 두-키 정렬: 타이브레이크(branch 오름차순) 먼저, 그다음 주 키(순간 내림차순).
    parseable.sort(key=lambda r: r["branch"])
    parseable.sort(key=lambda r: r["_instant"], reverse=True)
    unparseable.sort(key=lambda r: r["branch"])
    ordered = parseable + unparseable
    top = parseable[0]["_instant"] if parseable else None
    for r in ordered:
        # aware-datetime의 ==는 절대 순간을 비교하므로, 시간대가 달라도 동률이 맞는다.
        r["freshest"] = top is not None and r["_instant"] == top
    return ordered


def _peer_entry(driver, r, limit):
    return {
        "id": r["id"],
        "branch": r["branch"],
        "qualified_name": r["qualified_name"],
        "kind": _kind(r.get("labels")),
        "committed_at": r.get("committed_at"),
        "freshest": r["freshest"],
        # branch별 근거결박(_sources를 통해 author 생략) — 출처.
        "source_commit": r.get("source_commit"),
        "sources": _sources(r),
        # 표시 전용 의미 주석(외부 결박; verdict + confidence).
        "semantic": _peer_semantic(driver, r["id"], limit),
    }


# 벡터-KNN 회상의 유사도 하한(floor). Neo4j의 코사인 인덱스는 cosine >= 0에 대해 정규화된
# score = (1 + cosine) / 2를 반환하고, 음수 코사인은 0.5로 clamp한다(직교와 반대가 모두
# 0.5로 채점됨) — 경험적으로 검증했다. 이 하한 아래의 히트는 확신에 찬 매치가 아니므로,
# 낮은 유사도 답으로 k를 채우는 대신 명시적 간극으로 보고한다(ac-3 정직성). 0.6 ~ 코사인 0.2.
_MIN_COSINE_SCORE = 0.6


class InvalidQueryVector(ValueError):
    """유효한 EMBEDDING_DIM 코사인 벡터가 아닌 query_vector(길이가 틀렸거나 NaN/inf 포함).
    호출자가 원시 Neo4j 예외 대신 타입이 있는 거부를 받도록, 드라이버 질의 전에(BEFORE)
    제기한다(ac-5: 질의 벡터는 호출자가 제공한다 — palimpsest는 결코 임베딩하지 않는다)."""


def _validate_query_vector(query_vector) -> None:
    """형식이 잘못된 질의 벡터를 앞단에서 거부한다: 정확히 EMBEDDING_DIM개의 성분
    (인덱스 차원)을 가져야 하며 NaN/inf가 없어야 한다."""
    try:
        n = len(query_vector)
    except TypeError as exc:
        raise InvalidQueryVector("query_vector must be a sequence of floats") from exc
    if n != EMBEDDING_DIM:
        raise InvalidQueryVector(
            f"query_vector must have length {EMBEDDING_DIM}, got {n}"
        )
    if not all(math.isfinite(x) for x in query_vector):
        raise InvalidQueryVector("query_vector must not contain NaN or inf")


# Summary 벡터 인덱스 위에서 top-k 코사인, 그다음(summaries 채널처럼) (summary, 근거결박-ref)
# 당 한 행이라서 `_summary_channel`이 그룹핑하고 대상(TARGET) ref에서 stale을 설정할 수 있다.
# ``score``(인덱스의 코사인 유사도)와 대상의 ``branch``가 행마다 함께 실린다. branch 스코핑은
# reconcile의 ``branch IN $branches`` 필터를 반영한다(null $branches = 모든 평면); ORDER BY
# score DESC가 그룹핑 — 그리고 그럼으로써 summaries 채널 — 을 코사인 내림차순으로 유지한다.
_SEMANTIC_KNN = """
CALL db.index.vector.queryNodes($index_name, $k, $query_vector) YIELD node AS s, score
MATCH (s)-[:SUMMARIZES]->(tgt {id: s.target_id})
WHERE $branches IS NULL OR tgt.branch IN $branches
MATCH (s)-[r:SUMMARIZES]->(g)
RETURN s.id AS id, s.target_id AS target_id, s.claims AS claims,
       s.code_bound_at AS code_bound_at, s.semantic_verdict AS semantic_verdict,
       r.edge_kind AS edge_kind, score AS score, tgt.branch AS branch,
       g.id AS ref_id, g.source_commit AS source_commit, g.path AS path,
       g.start_line AS start_line, g.end_line AS end_line,
       g.committed_at AS committed_at
ORDER BY score DESC, id, ref_id
"""


def recall_semantic(driver, query_vector, branches=None, limit=25):
    """독립형 벡터-KNN 회상: 질의 벡터에 대한 top-k 코사인 Summary들.

    ``query_vector``는 호출자가 제공한 EMBEDDING_DIM 부동소수 벡터다(palimpsest는 결코
    임베딩하지 않는다 — ac-5 provider-free). Summary 벡터 인덱스 위에서 top-k 코사인 질의를
    돌리고, 형제 진입점들과 동일(SAME)한 유계 ``{items, sources, summaries, ...}`` 형태를
    반환한다; 매치된 Summary들이 별도 ``summaries`` 채널에 코사인 내림차순으로 드러나며,
    각각 다음을 지닌다:

    * ``score`` — 인덱스의 코사인 유사도로, 결과의 근거결박-커버리지 ``confidence``와
      분리(SEPARATE)해서 유지한다(코사인은 결코 confidence가 아니다, ac-3);
    * ``stale`` — 모든 Summary 노출 경로가 붙이는 신선도 플래그(SUMMARIZES 대상의 현재
      committed_at을 기준으로 :func:`_summary_channel`을 통해 설정); 그리고
    * ``branch`` — 히트가 온 평면(ADR-20260703 branch-scoped identity).

    ``branches``(기본 None = 모든 평면)는 KNN 후보 집합을 그 branch 평면들로 스코핑하여,
    전역 코사인 질의가 branch-scoped 평면들을 조용히 섞지 못하게 한다. ``limit``은 k를
    유계로 만든다(``queryNodes``가 k>=1을 요구하므로 >=1로 clamp). 유사도 하한 아래의 최선
    매치는 명시적 ``gap``이다 — 결코 확신에 찬 빈 답이나 낮은 유사도로 채운 k가 아니다
    (ac-3 정직성). 오직 조합적: 단일 벡터 질의 + dict 구성, 어디에도 LLM 없음.
    """
    _validate_query_vector(query_vector)
    k = max(1, limit)  # queryNodes는 k>=1을 요구한다(Cypher LIMIT 0과 달리)
    branch_filter = sorted(set(branches)) if branches is not None else None

    with driver.session() as session:
        rows = [
            r.data()
            for r in session.run(
                _SEMANTIC_KNN,
                index_name=VECTOR_INDEX_NAME,
                k=k,
                query_vector=list(query_vector),
                branches=branch_filter,
            )
        ]

    # 유사도 하한: 그 아래의 히트를 버린다(한 요약의 모든 행은 같은 score를 공유한다).
    # 그래서 k가 낮은 유사도의, 확신에 찬 빈 답으로 채워지는 일은 결코 없다.
    kept = [row for row in rows if row["score"] >= _MIN_COSINE_SCORE]
    # 요약별 (score, branch), ORDER BY score DESC 아래에서 첫 행이 이긴다.
    meta = {}
    for row in kept:
        meta.setdefault(row["id"], (row["score"], row["branch"]))

    entries = _summary_channel(kept)  # 그룹핑 + 대상 ref에서 stale 설정
    for entry in entries:
        score, branch = meta[entry["id"]]
        entry["score"] = score    # 코사인 유사도 — confidence와 분리(SEPARATE)
        entry["branch"] = branch  # 히트의 branch 평면(ADR-20260703)
    entries.sort(key=lambda e: e["score"], reverse=True)

    gaps = []
    if not entries:
        gaps = [
            f"no Summary within similarity floor {_MIN_COSINE_SCORE} of the query "
            f"vector (branches={branch_filter})"
        ]
    return _result([], gaps, None, entries)


def reconcile_recall(driver, symbol, branches, limit=25):
    """한 심볼의 branch-scoped 평면들 위에서의 N-방향 피어 reconcile 회상.

    ``symbol``의 branch-scoped 피어들을 정확히(EXACTLY) 호출자의 ``branches``에 걸쳐
    비교한다(ac-6) — 동등하게, 특권 branch 없이(NO). 피어는 자신의 ``committed_at``의
    절대 UTC 순간으로 순위 매겨진다(최신이 먼저; 중립적 branch-이름 타이브레이크는 안정성
    전용이다). 최대-순간 피어(들)에 ``freshest``가 표시된다. 각 피어는 자신의 branch별
    근거결박(author 생략)과, 이미 저장된 inferred 층에서 읽은 표시 전용(DISPLAY-ONLY) 의미
    주석(verdict + confidence + source_commit + code_bound_at)을 지닌다 — palimpsest는
    여기서 아무것도 생성하지 않는다(LLM/provider 호출 0).

    branch 간 충돌은 비생성적(non-generative) 두 갈래로, 구분해 라벨하여 드러난다:
    ``conflict_edges`` = 피어에 닿는 기존(EXISTING) CONFLICTS_WITH 엣지(relations 채널을
    통해, 결코 새로 만들지 않음); ``code_divergence`` = 피어들이 심볼을 공유하지만
    ``source_commit``이 다르다는 순수 계산 관찰. 반환:
    ``{symbol, branches, peers, code_divergence, conflict_edges, gaps}``.
    """
    branch_set = sorted(set(branches))
    with driver.session() as session:
        rows = [
            r.data()
            for r in session.run(_BRANCH_PEERS, symbol=symbol, branches=branch_set)
        ]

    if not rows:
        return {
            "symbol": symbol,
            "branches": branch_set,
            "peers": [],
            "code_divergence": {"source_commits": [], "diverged": False},
            "conflict_edges": [],
            "gaps": [f"symbol '{symbol}' has no peers in branches {branch_set}"],
        }

    ordered = _ranked_peers(rows)
    peers = [_peer_entry(driver, r, limit) for r in ordered]

    # 갈래 (b): 구조적 발산(divergence) — 순수 계산 관찰(엣지를 쓰지 않고,
    # edge_kind='inferred' 세탁도 없음). 피어들은 심볼을 공유한다; source_commit이
    # 다르면 코드가 branch 간에 발산한 것이다.
    source_commits = sorted({r.get("source_commit") for r in rows if r.get("source_commit")})
    code_divergence = {
        "source_commits": source_commits,
        "diverged": len(source_commits) > 1,
    }

    # 갈래 (a): 피어에 닿는 기존(EXISTING) CONFLICTS_WITH 엣지, relations 채널을 통해
    # 드러남(결코 새로 만들지 않음). 계산된 발산과는 구별된다.
    peer_items = [{"id": r["id"]} for r in ordered]
    conflict_edges = [
        e for e in _relations(driver, peer_items, limit)
        if e["rel_type"] == "CONFLICTS_WITH"
    ]

    return {
        "symbol": symbol,
        "branches": branch_set,
        "peers": peers,
        "code_divergence": code_divergence,
        "conflict_edges": conflict_edges,
        "gaps": [],
    }


# ── churn / co-change: MODIFIES(Episode -> File) 회상 채널 ─────────
# MODIFIES는 결정론적이지만 의도적으로 DEFAULT_RELATIONS에 없다(ABSENT). 그래서 author를
# 지닌 Episode가 일반 items 순회로 끌려 들어가는 일은 결코 없다. 이 두 별도(SEPARATE)
# 진입점은 그것을 안전하게 드러낸다: Episode를 결코 projection하지 않는다(``RETURN e`` /
# ``e.author`` 없음) — 오직 File 끝점만, :func:`_sources`를 통해(author 생략), summaries /
# community 채널과 똑같이. 순위는 순수 count DESC + 전순서(id) 타이브레이크이므로, 새롭거나
# 희소한 repo도 하드코딩된 count 임계값 없이 우아하게 저하한다(핫스팟이 적어질 뿐).

# 핫스팟 File들, 몇 개의 서로 다른(DISTINCT) 커밋(Episode)이 건드렸는지로 순위. ``e``는
# 오직 ``count(DISTINCT e)`` 안에서만 쓰이며 — 결코 반환되지 않으므로, author 누출이 없다.
_CHURN = """
MATCH (f:File)<-[:MODIFIES]-(e:Episode)
WITH f, count(DISTINCT e) AS churn
RETURN f.id AS id, labels(f) AS labels, f.name AS name,
       f.qualified_name AS qualified_name,
       f.path AS path, f.start_line AS start_line, f.end_line AS end_line,
       f.source_commit AS source_commit, f.committed_at AS committed_at,
       churn AS churn
ORDER BY churn DESC, id
LIMIT $lim
"""

# Episode별로 co-change 파일로 뻗는 fan-out의 상한: 수천 개 파일을 건드리는 메가-커밋이
# co-change 조인을 터뜨리면 안 된다. 호출자의 ``limit``은 반환되는(RETURNED) 행을 유계로
# 하고, 이 이름 있는 cap은 각 Episode가 기여하는 중간(INTERMEDIATE) 확장을 유계로 한다
# (존재해야 하는 임계값은 질의에 묻힌 매직 리터럴이 아니라 이름 있는 모듈 상수가 된다).
_COCHANGE_FANOUT_CAP = 512

# 시드 File과 co-change한 File들: 같은(SAME) Episode가 건드린 또 다른 File. ``f2``는 시드
# 자신의(OWN) branch 평면으로 제한된다(``coalesce``가 맨 null 평면을 처리한다). 그래서
# 맨 Episode가 두 branch-scoped 평면을 잇는(bridge) 일은 결코 없다(recall_semantic의 branch
# 가드를 반영한다). Episode는 여기서도 결코 projection되지 않는다 — 오직 File2 끝점만
# 드러난다. Episode별 fan-out은 서버측에서 cap된다.
_COCHANGE = """
MATCH (f:File {id: $id})<-[:MODIFIES]-(e:Episode)
CALL {
    WITH e, f
    MATCH (e)-[:MODIFIES]->(f2:File)
    WHERE f2.id <> f.id
      AND coalesce(f2.branch, '') = coalesce(f.branch, '')
    RETURN f2 ORDER BY f2.id LIMIT $fanout
}
WITH f2, count(DISTINCT e) AS cochange
RETURN f2.id AS id, labels(f2) AS labels, f2.name AS name,
       f2.qualified_name AS qualified_name,
       f2.path AS path, f2.start_line AS start_line, f2.end_line AS end_line,
       f2.source_commit AS source_commit, f2.committed_at AS committed_at,
       cochange AS cochange
ORDER BY cochange DESC, id
LIMIT $lim
"""


def recall_churn(driver, limit=25):
    """churn 핫스팟을 회상한다 — 몇 개의 커밋이 건드렸는지로 순위 매긴 File들.

    MODIFIES 척추(spine) 위의 별도(SEPARATE) 전역 진입점. 표준
    ``{items, sources, summaries, ...}`` 형태를 반환한다; 각 항목은 ``churn`` 카운트를
    지닌 핫스팟 File(근거결박, author 생략)이며, count DESC에 id 타이브레이크로 정렬되고
    (결정론적, 실행 간 안정적) ``limit``으로 유계다. 빈 MODIFIES 그래프는 크래시가 아니라
    명시적 간극이다(graceful-empty). 오직 조합적(단일 집계 질의 + dict 구성) — LLM 없음.
    """
    with driver.session() as session:
        rows = [r.data() for r in session.run(_CHURN, lim=limit)]
    items = [_item(rec, MODIFIES, 1) for rec in rows]
    for it, rec in zip(items, rows):
        it["churn"] = rec["churn"]
    gaps = [] if items else ["no MODIFIES edges in the graph — churn recall is empty"]
    return _result(items, gaps, None, [])


def recall_cochange(driver, file_id, limit=25):
    """``file_id``과 co-change한 File들을 회상한다(같은-커밋 co-change).

    별도(SEPARATE) 진입점: 시드 File과 같은(SAME) Episode가 건드린 File2들이며, co-change
    카운트 DESC(id 타이브레이크)로 순위 매겨지고, 시드 자신의 branch 평면으로 제한되며,
    ``limit``과 Episode별 fan-out cap으로 유계다. 해석되지 않은 시드 / co-change 없음은
    확신에 찬 빈 답이 아니라 명시적 간극이다. Episode는 결코 projection되지 않는다(author
    생략). 오직 조합적.
    """
    with driver.session() as session:
        if session.run(_RESOLVE, id=file_id).single() is None:
            gap = f"file '{file_id}' did not resolve to any node in the graph"
            return _result([], [gap], None, [])
        rows = [
            r.data()
            for r in session.run(
                _COCHANGE, id=file_id, lim=limit, fanout=_COCHANGE_FANOUT_CAP
            )
        ]
    items = [_item(rec, MODIFIES, 1) for rec in rows]
    for it, rec in zip(items, rows):
        it["cochange"] = rec["cochange"]
    gaps = [] if items else [f"file '{file_id}' has no co-changed files"]
    return _result(items, gaps, None, [])
