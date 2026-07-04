"""Class 단위 Community 탐지 (결정론적, provider-free).

Community는 Class들의 연결 요소(connected component)다. 두 Class는 RESOLVED된
결정론적 IR에서 둘 사이에 cross-class 엣지가 있을 때만(iff) 연결된 것으로 본다:

  * ``DEPENDS_ON`` (Class -> Class), 또는
  * ``CALLS`` (Method -> Method)를 ``Class-[CONTAINS]->Method`` 엣지를 통해
    선언 Class로 끌어올린 것(self-edge — 같은 선언 Class — 은 버린다).

이 분할은 배타적(EXCLUSIVE)이고 평면적(FLAT)이다: 모든 Class는 정확히 하나의
Community에 속하며, 고립된 Class는 자기 자신만으로 singleton을 이룬다. 탐지는
IR 위에서 순수 Python(union-find)으로 한다 — GDS 없음, LLM 없음.

Reconcile는 *materialize-in-IR* 방식이다: :func:`augment_communities`가
``Community`` 노드와 ``Class-[MEMBER_OF]->Community`` 엣지를 IR에 덧붙여, 일반
``ingest`` writer가 다른 노드/엣지와 똑같이 이들을 영속화하고 provenance를 찍게
한다(``edge_kind='deterministic'``는 자동으로 설정된다).
"""

from __future__ import annotations

import hashlib

from palimpsest.ir import (
    CALLS,
    CLASS,
    COMMUNITY,
    CONTAINS,
    DEPENDS_ON,
    MEMBER_OF,
    METHOD,
    Edge,
    IR,
    Node,
    Provenance,
)


def community_id(members) -> str:
    """결정론적이고 네임스페이스로 격리된 Community id.

    ``raw``는 정렬된 멤버 Class qualified_name들을 NUL로 이어붙인 것이라, id는
    멤버 순서에 무관하다 — rebuild-stable(ac-3). ``community:`` 프리픽스는 id가
    코드 ``qualified_name``과 절대 충돌하지 않음을 보장한다.
    :func:`palimpsest.kg.summary.summary_id`와 동일한 방식이다.
    """
    raw = "\x00".join(sorted(members))
    return "community:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _class_of_method(ir: IR) -> dict[str, str]:
    """CONTAINS를 통해 각 Method qualified_name을 그 선언 Class로 매핑한다.

    CONTAINS는 오버로드되어 있다(Repo->Package와 Class->Method); 양쪽 endpoint를
    노드 색인과 대조해 Class->Method 엣지만 남긴다.
    """
    class_ids = {n.qualified_name for n in ir.nodes_of(CLASS)}
    method_ids = {n.qualified_name for n in ir.nodes_of(METHOD)}
    return {
        e.dst: e.src
        for e in ir.edges_of(CONTAINS)
        if e.src in class_ids and e.dst in method_ids
    }


def _class_level_pairs(ir: IR) -> set[frozenset]:
    """무방향 cross-class 링크: DEPENDS_ON(Class->Class)에, 선언 Class로 끌어올린
    CALLS를 더한 것(self-link은 버린다). 입력은 RESOLVED된 IR 엣지들이다."""
    class_ids = {n.qualified_name for n in ir.nodes_of(CLASS)}
    m2c = _class_of_method(ir)
    pairs: set[frozenset] = set()
    for e in ir.edges_of(DEPENDS_ON):
        if e.src in class_ids and e.dst in class_ids and e.src != e.dst:
            pairs.add(frozenset((e.src, e.dst)))
    for e in ir.edges_of(CALLS):
        cs, cd = m2c.get(e.src), m2c.get(e.dst)
        if cs and cd and cs != cd:
            pairs.add(frozenset((cs, cd)))
    return pairs


def compute_communities(ir: IR) -> list[list[str]]:
    """IR의 Class들을 연결 요소로 분할한다(union-find).

    결정론적으로 정렬된 Community 목록을 반환한다. 각 Community는 멤버 Class
    qualified_name을 정렬한 리스트다. 모든 Class는 정확히 하나에만 나타난다.
    """
    class_ids = sorted(n.qualified_name for n in ir.nodes_of(CLASS))
    parent = {c: c for c in class_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # 사전순으로 더 큰 root를 더 작은 root 아래에 붙여, 요소의 root가
            # 순서에 무관하도록(rebuild-stable) 만든다.
            parent[max(ra, rb)] = min(ra, rb)

    for pair in _class_level_pairs(ir):
        a, b = tuple(pair)
        union(a, b)

    groups: dict[str, list[str]] = {}
    for c in class_ids:
        groups.setdefault(find(c), []).append(c)
    return sorted(sorted(members) for members in groups.values())


def augment_communities(ir: IR, provenance: Provenance) -> IR:
    """Class 단위 Community 분할을 ``ir``에 materialize한다(제자리에서).

    요소마다 ``Community`` 노드 하나를, 멤버마다 ``Class-[MEMBER_OF]->
    Community`` 엣지 하나를 덧붙인다. 각각에는 corpus 단위 ``provenance``를
    찍는다(Community 노드는 Repo / Package처럼 path/line 근거를 갖지 않는다).
    이후 기존 ``ingest``가 이들을 일반적으로 영속화한다.
    """
    for members in compute_communities(ir):
        cid = community_id(members)
        ir.nodes.append(
            Node(kind=COMMUNITY, qualified_name=cid, name=cid, provenance=provenance)
        )
        for cls in members:
            ir.edges.append(
                Edge(kind=MEMBER_OF, src=cls, dst=cid, provenance=provenance)
            )
    return ir
