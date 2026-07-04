"""추출 IR을 Neo4j로 일괄 적재한다 (raw Cypher, UNWIND + MERGE).

결정론적이고 최소한인 온톨로지 (승인된 설계 참조):

  Node labels : Repo, Package, File, Class, Method, Episode(commit)
  Rel types   : CONTAINS, CALLS, DEPENDS_ON, IMPORTS

정체성은 IR의 ``qualified_name`` (``Node.id``)이다: 라벨마다 ``id``에 대한
uniqueness CONSTRAINT 하나를 두고, 모든 쓰기는 MERGE-on-id이므로 재적재는
멱등(idempotent)하다 (git이 진리의 원천이고, Neo4j 투영은 재구축 가능하다).

모든 노드/엣지에는 git provenance(source_commit / author / committed_at)와
신선도(``code_bound_at`` — v1 단일 커밋 = 적재된 커밋의 committed_at)가 찍힌다.
모든 엣지는 추가로 ``edge_kind = "deterministic"``를 지닌다: v1에는 구조적·결정론적
엣지만 있으며, 이 속성이 존재하는 덕에 이후의 inferred 층이 이들과 절대 혼동될 수
없다 (스키마로 강제되는 no-laundering 분리).
"""

from __future__ import annotations

from collections import defaultdict

from palimpsest.ir import (
    IR,
    REPO,
    PACKAGE,
    FILE,
    CLASS,
    METHOD,
    COMMUNITY,
    CONTAINS,
    IMPORTS,
    CALLS,
    DEPENDS_ON,
    MEMBER_OF,
    MODIFIES,
    SUMMARY,
    RISK,
    DESIGN_DECISION,
    EDGE_KIND_DETERMINISTIC,
)

# 온톨로지, 닫혀 있고 명시적이다 (데이터에서 나온 동적 라벨 없음). ``Summary``,
# ``Risk``, ``DesignDecision``은 inferred 층 라벨이다; 결정론적 IR 노드를 지니지
# 않지만, ``create_constraints``가 이들의 uniqueness CONSTRAINT를 마련하도록 여기
# 나열한다. ``Community``는 ``augment_communities``가 IR에 구현하는 결정론적 노드다.
# 이들의 inferred 엣지(SUMMARIZES / RISKS / DECIDES / SUPERSEDES / ADDRESSES_RISK)는
# ``REL_TYPES``에서 의도적으로 빠져 있다 — 범용 writer가 이들에 절대
# ``edge_kind='deterministic'``를 찍어선 안 되며, 전용 로더가 inferred로 쓴다.
# N-way 브랜치 캡처의 부분 캡처 정직성(partial-capture honesty)을 기록하는
# provider-free 구조 라벨이다 (``reconcile``이 쓰며, IR 노드 종류가 아니다).
# ``create_constraints``가 이것의 ``id`` uniqueness CONSTRAINT를 마련하도록만 나열한다.
CAPTURE_MANIFEST = "CaptureManifest"

NODE_LABELS = [
    REPO, PACKAGE, FILE, CLASS, METHOD, "Episode", SUMMARY, COMMUNITY, RISK,
    DESIGN_DECISION, CAPTURE_MANIFEST,
]
# MODIFIES는 결정론적 rel type이지만, 전용 로더(``ingest_modifies``)가 쓰며 범용
# ``_REL_MERGE`` 경로로는 절대 쓰지 않는다: src가 ``ir.nodes`` 바깥에 사는 맨
# Episode(커밋 SHA)라서, ``ingest``의 ``id_to_label`` 맵에 항목이 없고 범용 경로는
# 모든 MODIFIES 엣지를 무음으로 드롭하게 된다. 온톨로지 레지스트리 용도로만 나열한다.
REL_TYPES = [CONTAINS, IMPORTS, CALLS, DEPENDS_ON, MEMBER_OF, MODIFIES]

# 라벨마다 MERGE-on-id 하나; 속성 SET은 균일하다 (쓰이지 않는 속성은 null로 풀리고
# Neo4j가 이를 드롭한다 — Repo/Package는 그저 path/line grounding을 지니지 않는다).
_NODE_MERGE = """
UNWIND $rows AS row
MERGE (n:`{label}` {{id: row.id}})
SET n.name          = row.name,
    n.qualified_name = row.qualified_name,
    n.branch        = row.branch,
    n.path          = row.path,
    n.start_line    = row.start_line,
    n.end_line      = row.end_line,
    n.source_commit = row.source_commit,
    n.author        = row.author,
    n.committed_at  = row.committed_at,
    n.code_bound_at = row.code_bound_at
"""

# 엔드포인트는 라벨별로 MATCH된다(merge 아님): target이 미해결/외부인 엣지(예:
# IMPORTS java.util.Map — 소스만 보는 파서에겐 정직한 결과)는 타입 있는 IR 노드가
# 없으므로, 타입 없는 유령 노드로 구현되는 대신 쿼리 전에 ``ingest``에서 드롭된다.
# MATCH가 엔드포인트의 라벨을 지녀 라벨별 id uniqueness 인덱스를 쓴다 (라벨 없는
# ``MATCH ({id: ...})``는 그럴 수 없다 — Neo4j 5에는 라벨 없는 속성 인덱스가 없어
# AllNodesScan으로 계획되고, 그래프가 커질수록 backfill이 초선형이 된다).
_REL_MERGE = """
UNWIND $rows AS row
MATCH (a:`{src_label}` {{id: row.src}})
MATCH (b:`{dst_label}` {{id: row.dst}})
MERGE (a)-[r:`{rel}`]->(b)
SET r.edge_kind     = $edge_kind,
    r.source_commit = row.source_commit,
    r.author        = row.author,
    r.committed_at  = row.committed_at,
    r.code_bound_at = row.committed_at
"""

_EPISODE_MERGE = """
UNWIND $rows AS row
MERGE (e:Episode {id: row.id})
SET e.name          = row.id,
    e.qualified_name = row.id,
    e.source_commit = row.id,
    e.author        = row.author,
    e.committed_at  = row.committed_at,
    e.code_bound_at = row.committed_at
"""

# Episode -[:MODIFIES]-> File, 아래 전용 로더가 쓴다. 양쪽 엔드포인트 모두
# MATCH된다(절대 merge 아님): Episode는 그 커밋 자신의 ingest가 쓰고, File은
# HEAD-투영 노드다 — File 노드가 없는 변경 경로(예: 삭제된 뒤 다시 추가되지 않음)는
# 유령 File로 구현되는 대신 무음으로 건너뛴다 (ac-2: File은 HEAD-MERGE 불변식을
# 유지한다). 엣지 MERGE는 멱등하므로, 재적재 / 재backfill이 중복 없이 수렴한다.
# ``count(r)``는 실제로 안착한 엣지 수를 보고한다 (File이 해결되지 않은 행은 행을
# 만들지 않는다).
_MODIFIES_MERGE = """
UNWIND $rows AS row
MATCH (e:Episode {id: row.episode_id})
MATCH (f:File {id: row.file_id})
MERGE (e)-[r:MODIFIES]->(f)
SET r.edge_kind     = $edge_kind,
    r.source_commit = row.episode_id,
    r.committed_at  = row.committed_at,
    r.code_bound_at = row.committed_at
RETURN count(r) AS n
"""


def ingest_modifies(driver, rows) -> int:
    """전용 로더로 Episode -[:MODIFIES]-> File 엣지를 쓴다.

    ``rows``는 ``{episode_id, file_id, committed_at}``의 리스트다. 실제로 안착한
    엣지 수를 반환한다 (File id에 HEAD 노드가 없는 행은 건너뛰고, 절대 유령 File을
    만들지 않는다 — ac-2). 멱등하며(엣지 MERGE), backfill을 재실행해도 수렴한다.
    결정론적이고 provider-free다 (어디에도 LLM 없음).
    """
    if not rows:
        return 0

    def _write(tx):
        rec = tx.run(
            _MODIFIES_MERGE, rows=list(rows), edge_kind=EDGE_KIND_DETERMINISTIC
        ).single()
        return rec["n"] if rec else 0

    with driver.session() as session:
        return session.execute_write(_write)


# 브랜치 평면 GC, 둘 다 ``branch`` 노드 속성을 키로 삼는다 (INGEST 계약).
#
# (2a) scoped-rebuild: 지정된 브랜치의 평면 전체를 지운 뒤 다시 투영한다
# (delete-then-project). 그래서 shrink/rebase/tip-이동이 오래된 노드를 남기지
# 않는다. 브랜치 backfill 시작 시 한 번만 실행한다 (create_constraints처럼), 절대
# 커밋마다 하지 않는다 — 커밋마다 지우면 그 브랜치 자신의 앞선 커밋을 지우게 된다.
_WIPE_BRANCH_PLANE = "MATCH (n {branch:$branch}) DETACH DELETE n"

# (2b) reaper: git에 없는 브랜치의 지정 브랜치 평면을 모두 드롭한다.
# ``branch IS NOT NULL`` 가드 덕에 맨-id(미지정) 평면은 절대 reap되지 않는다 (ac-6)
# — ``$live``가 비어 있어도 맨 평면은 살려둔다.
_REAP_DEAD_BRANCHES = (
    "MATCH (n) WHERE n.branch IS NOT NULL AND NOT n.branch IN $live "
    "DETACH DELETE n"
)


def wipe_branch_plane(driver, branch: str) -> None:
    """지정 브랜치의 평면 전체를 다시 투영하기 전에 지운다 (2a).

    ``branch``는 반드시 실제 브랜치 이름이어야 한다 — 절대 ``None``이 아니다.
    ``None``을 지우면 맨-id 평면과 매치되는데, 이 평면은 MERGE-누적이므로 reap되면
    안 된다.
    """
    if branch is None:
        raise ValueError("wipe_branch_plane requires a branch name, not None")
    with driver.session() as session:
        session.run(_WIPE_BRANCH_PLANE, branch=branch)


def reap_dead_branches(driver, live) -> None:
    """브랜치가 ``live``에 없는 지정 브랜치 평면을 모두 드롭한다 (2b).

    ``live`` = git에 존재하는 추적 브랜치 이름들. 맨-id 평면은 ``branch IS NOT
    NULL`` 가드로 살려둔다.
    """
    with driver.session() as session:
        session.run(_REAP_DEAD_BRANCHES, live=list(live))


def create_constraints(driver) -> None:
    """노드 라벨마다 결정론적 ``id``에 대한 uniqueness CONSTRAINT 하나."""
    with driver.session() as session:
        for label in NODE_LABELS:
            session.run(
                f"CREATE CONSTRAINT `{label.lower()}_id_unique` IF NOT EXISTS "
                f"FOR (n:`{label}`) REQUIRE n.id IS UNIQUE"
            )


def _node_row(node) -> dict:
    p = node.provenance
    return {
        "id": node.id,
        "name": node.name,
        "qualified_name": node.qualified_name,
        # 브랜치 네임스페이스 (GC 판별자); 맨-id 평면에서는 null.
        "branch": node.branch,
        "path": node.path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "source_commit": p.source_commit,
        "author": p.author,
        "committed_at": p.committed_at,
        # 신선도 — v1 단일 커밋: 적재된 커밋의 시각에 결박된다.
        "code_bound_at": p.committed_at,
    }


def _edge_row(edge) -> dict:
    p = edge.provenance
    return {
        "src": edge.src,
        "dst": edge.dst,
        "source_commit": p.source_commit,
        "author": p.author,
        "committed_at": p.committed_at,
    }


def _episode_rows(ir: IR) -> list[dict]:
    seen: dict[str, dict] = {}
    for n in ir.nodes:
        p = n.provenance
        seen.setdefault(
            p.source_commit,
            {"id": p.source_commit, "author": p.author,
             "committed_at": p.committed_at},
        )
    return list(seen.values())


def ingest(driver, ir: IR) -> None:
    """``driver``를 통해 ``ir``을 Neo4j로 멱등하게 적재한다.

    하나의 쓰기 트랜잭션: Episode(들), 그다음 라벨별로 MERGE된 노드, 그다음 rel-type
    별로 MERGE된 엣지 (트랜잭션 안에서 이미 쓴 엔드포인트는 MATCH에 보인다).
    """
    nodes_by_label = {label: [] for label in NODE_LABELS}
    for n in ir.nodes:
        nodes_by_label[n.kind].append(_node_row(n))

    # 각 엔드포인트의 라벨을 이 IR에서 해결해, 관계 MERGE가 라벨로 MATCH할 수 있게
    # 한다(인덱스 사용). 엣지는 양쪽 엔드포인트가 모두 실제 IR 노드일 때만 구현된다;
    # 미해결 엔드포인트(타입 노드 없는 외부 target)는 여기서 건너뛴다 — 예전의 라벨
    # 없는 MATCH가 만들던 바로 그 드롭이다. 그룹핑은 (rel_type, src_label,
    # dst_label)을 키로 삼아, 각 그룹이 인덱스 쿼리 하나로 실행되게 한다.
    id_to_label = {n.id: n.kind for n in ir.nodes}
    edges_by_group: dict = defaultdict(list)
    for e in ir.edges:
        src_label = id_to_label.get(e.src)
        dst_label = id_to_label.get(e.dst)
        if src_label is None or dst_label is None:
            continue
        edges_by_group[(e.kind, src_label, dst_label)].append(_edge_row(e))

    episodes = _episode_rows(ir)

    def _write(tx):
        if episodes:
            tx.run(_EPISODE_MERGE, rows=episodes)
        for label, rows in nodes_by_label.items():
            if rows:
                tx.run(_NODE_MERGE.format(label=label), rows=rows)
        for (rel, src_label, dst_label), rows in edges_by_group.items():
            tx.run(
                _REL_MERGE.format(
                    rel=rel, src_label=src_label, dst_label=dst_label
                ),
                rows=rows,
                edge_kind=EDGE_KIND_DETERMINISTIC,
            )

    with driver.session() as session:
        session.execute_write(_write)
