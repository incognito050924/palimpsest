"""정적 코드 추출을 위한 중간 표현(IR, Intermediate Representation).

이후 Neo4j 적재 노드가 소비하는 직렬화 가능한 도메인 모델이다. 소스에서 추출한
결정론적 구조 온톨로지를 담는다:

  노드: Repo, Package, File, Class, Method
  엣지: CONTAINS, IMPORTS, CALLS, DEPENDS_ON

모든 노드와 엣지는 git ``Provenance``(source_commit / author / committed_at)를 담으며,
이는 고정된(pinned) 적재 커밋에 대해 한 번만 읽는다.

노드 정체성은 결정론적 ``qualified_name``이다:
  - Package : FQN                     (kr.co.ecoletree.service.commute.service)
  - File    : repo 상대 경로          (src/main/java/.../CommuteService.java)
  - Class   : package.Class           (kr.co.ecoletree...service.CommuteService)
  - Method  : package.Class#m(types)  (...CommuteService#insertGotoWork(Map,HttpServletRequest))

구조는 평범한 dataclass이며, ``to_dict()``는 dict/JSON 직렬화 가능한 출력을 낸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

# Unit Separator(0x1f) — branch 네임스페이스를 qualified_name과 이어 붙여
# branch-scoped id를 만든다. ``summary:`` / ``community:`` 프리픽스처럼, scoped id가
# 순수 qualified_name과 절대 충돌하지 않도록 보장한다.
_BRANCH_US = "\x1f"


def branch_scoped_id(branch: Optional[str], qualified_name: str) -> str:
    """노드 id와 엣지 끝점이 공유하는 순수(pure) 정체성 함수.

    ``branch=None``은 순수 ``qualified_name``을 그대로 반환한다(단일 브랜치 캡처와
    바이트 단위로 동일 — additive). 이름 있는 브랜치는 MERGE 키에
    ``branch:<branch>\\x1f<qualified_name>`` 형태로 접혀 들어가, 한 심볼의 N개 브랜치가
    서로 다른 노드로 공존한다(ac-1). 노드 id와 엣지 src/dst에 같은 함수를 쓰므로
    id와 끝점이 일관되고 캡처 순서에 불변(capture-order-invariant)이다.
    """
    if branch is None:
        return qualified_name
    return f"branch:{branch}{_BRANCH_US}{qualified_name}"

# 노드 종류(kind)
REPO = "Repo"
PACKAGE = "Package"
FILE = "File"
CLASS = "Class"
METHOD = "Method"
# Community는 Class 수준에서 (클래스 간 CALLS / DEPENDS_ON를 통해) 연결 성분
# (connected component)을 이루는 Class들을 묶는다 — 결정론적이고 재빌드에 안정적인
# (rebuild-stable) 분할이다.
COMMUNITY = "Community"

# 엣지 종류(결정론적 구조 온톨로지)
CONTAINS = "CONTAINS"
IMPORTS = "IMPORTS"
CALLS = "CALLS"
DEPENDS_ON = "DEPENDS_ON"
# 한 Class는 정확히 하나의 Community에 MEMBER_OF다(배타적, 평평한 분할).
MEMBER_OF = "MEMBER_OF"
# Episode(하나의 커밋)는 그 커밋이 바꾼 File(들)을 MODIFIES한다 — churn(변경량) /
# co-change(동시 변경)의 척추. 결정론적이다(``git diff-tree``에서 도출, 판단 없음):
# src는 순수 커밋 스코프 Episode(``ir.nodes`` 항목이 절대 아님), dst는 branch-scoped
# File id다. 전용(dedicated) 로더가 기록하며(Episode는 일반 노드/엣지 경로 밖에 산다),
# 회상(recall)의 순회 화이트리스트에서는 의도적으로 빠져 있다 — churn 엣지가 author를
# 지닌 Episode를 아이템으로 끌어들이지 않도록.
MODIFIES = "MODIFIES"

# 추론된(inferred) 의미층: 외부에서 생성된 요약(palimpsest은 provider-free —
# LLM을 절대 호출하지 않는다. 요약은 다른 곳에서 만들어져 근거결박(grounded) 적재를 위해
# 넘겨받는다). Summary 노드는 자신이 근거하는 코드를 SUMMARIZES한다.
SUMMARY = "Summary"          # node label
SUMMARIZES = "SUMMARIZES"    # edge type

# 추론된 의미층(1급 판단 엔티티): 외부에서 생성된 Risk — "이 코드는 위험하다"는
# 판단으로 자체 정체성을 가진다. Risk 노드는 자신이 플래그한 코드 노드(들)를 RISKS한다.
# Summary처럼 다른 곳에서 생성되어 근거결박 적재를 위해 넘겨받는다. palimpsest은 절대
# 판단하지 않는다.
RISK = "Risk"                # node label
RISKS = "RISKS"              # edge type

# 추론된 의미층(1급 결정 엔티티): 외부에서 생성된 DesignDecision —
# "이것은 설계 결정이다"이며 자체 정체성을 가진다. 코드 노드(들)나 다른 결정을 DECIDES하고,
# 다른 결정을 SUPERSEDES하며, Risk 노드를 ADDRESSES_RISK한다. Risk처럼 다른 곳에서
# 생성되어 근거결박 적재를 위해 넘겨받는다. palimpsest은 절대 판단하지 않는다.
DESIGN_DECISION = "DesignDecision"    # node label
DECIDES = "DECIDES"                   # edge type (DesignDecision -> code | DesignDecision)
SUPERSEDES = "SUPERSEDES"            # edge type (DesignDecision -> DesignDecision)
ADDRESSES_RISK = "ADDRESSES_RISK"    # edge type (DesignDecision -> Risk)

# 두 개의 기존(EXISTING) 엔티티 사이의 추론된 일반(GENERIC) 관계(새 노드 없음):
# 외부 생성기가 관계를 주장하면, palimpsest은 그것을 근거결박으로(양 끝점이 모두 resolve)
# edge_kind='inferred'로 적재한다. rel_type은 이 닫힌 집합으로 제한된다 — 쿼리에
# 자유 형식 데이터가 절대 들어가지 않는다.
CAUSALLY_RELATES = "CAUSALLY_RELATES"   # directed: cause -> effect
RELATES_TO = "RELATES_TO"               # association
CONFLICTS_WITH = "CONFLICTS_WITH"       # conflict (숨은 의도 충돌 표시)
INFERRED_RELATION_TYPES = frozenset({CAUSALLY_RELATES, RELATES_TO, CONFLICTS_WITH})

# ``edge_kind`` 마커 — 결정론적 구조층과 추론된 의미층 사이의, 스키마로 강제되는
# no-laundering(세탁 금지) 분리다. 두 edge_kind 상수가 한 곳에 살도록 두 값을 여기
# 함께 둔다.
EDGE_KIND_DETERMINISTIC = "deterministic"
EDGE_KIND_INFERRED = "inferred"

# 단일 공유 임베딩 차원. 이 하나의(ONE) 상수를 Neo4j VECTOR INDEX DDL과 요약별 차원
# 검증기가 함께(BOTH) 사용한다 — 리터럴 두 개를 두지 않는다: Neo4j는 크기가 인덱스
# 차원과 다른 벡터 속성을 조용히 건너뛴다(drift 시 silent-unsearchable). 그래서 가드와
# 인덱스는 구성상(by construction) 일치해야 한다. palimpsest은 provider-free다:
# 임베딩은 페이로드에 실려 도착하고(다른 곳에서 생성), palimpsest은 절대 생성하지 않는다.
EMBEDDING_DIM = 1536


@dataclass(frozen=True)
class Provenance:
    """고정된 적재 커밋에 대한 git 근거결박, 한 번만 읽는다."""

    source_commit: str
    author: str
    committed_at: str

    def to_dict(self) -> dict:
        return {
            "source_commit": self.source_commit,
            "author": self.author,
            "committed_at": self.committed_at,
        }


@dataclass
class Node:
    """구조 엔티티. ``qualified_name``이 정체성이다."""

    kind: str
    qualified_name: str
    name: str
    provenance: Provenance
    # file:line 근거결박 — File / Class / Method에는 설정, Repo / Package에는 None
    path: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    # 정체성(MERGE 키)에 접혀 들어간 branch 네임스페이스. 순수 단일 브랜치 평면에서는
    # None. ``scope_to_branch``가 설정하며, 노드 속성으로 영속화된다(GC 판별자).
    branch: Optional[str] = None

    @property
    def id(self) -> str:
        return branch_scoped_id(self.branch, self.qualified_name)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "qualified_name": self.qualified_name,
            "name": self.name,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class Edge:
    """노드 정체성 사이의 방향 있는 관계.

    ``dst``는 노드의 ``qualified_name``이다. IMPORTS의 경우 대응하는 노드가 없는 외부
    qualified name을 가리킬 수 있다(미해결 / 외부 코드) — 소스만 보는 파서에게는 이것이
    정직한 표현이다.
    """

    kind: str
    src: str
    dst: str
    provenance: Provenance

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "src": self.src,
            "dst": self.dst,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class IR:
    """추출 결과: 노드와 엣지의 집합."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    # --- 편의 조회(테스트 / 적재에서 사용) ---

    def nodes_of(self, kind: str) -> list[Node]:
        return [n for n in self.nodes if n.kind == kind]

    def node(self, qualified_name: str) -> Optional[Node]:
        for n in self.nodes:
            if n.qualified_name == qualified_name:
                return n
        return None

    def edges_of(self, kind: str) -> list[Edge]:
        return [e for e in self.edges if e.kind == kind]

    def has_edge(self, kind: str, src: str, dst: str) -> bool:
        return any(
            e.kind == kind and e.src == src and e.dst == dst for e in self.edges
        )


def scope_to_branch(ir: IR, branch: Optional[str]) -> IR:
    """순수 변환: 정체성이 ``branch``로 접혀 들어간 새(NEW) IR을 반환한다.

    모든 코드 Node에 ``branch``를 찍고, 모든 엣지의 src/dst를 같은 순수 함수
    (:func:`branch_scoped_id`)로 다시 써서 scoped 노드 id와 엣지 끝점이 일관되고
    캡처 순서에 불변으로 유지된다. 입력 IR은 변형되지 않으므로, 한 번 추출한 IR을 여러
    브랜치로 팬아웃할 수 있다. ``branch=None``은 바이트 단위로 동일한 복사본을 낸다
    (Episode는 순수한 채로 남는다 — 커밋 스코프이며 적재 시 provenance에서 도출되고,
    ``ir.nodes``에는 절대 없다).
    """
    nodes = [replace(n, branch=branch) for n in ir.nodes]
    edges = [
        replace(
            e,
            src=branch_scoped_id(branch, e.src),
            dst=branch_scoped_id(branch, e.dst),
        )
        for e in ir.edges
    ]
    return IR(nodes=nodes, edges=edges)


@dataclass(frozen=True)
class SummaryClaim:
    """:class:`Summary` 안의 근거결박된 주장 하나.

    ``source_refs``는 노드 id(심볼 ``qualified_name`` 또는 repo 상대 파일 경로)이며,
    각각이 실제 그래프 노드로 resolve되어야 한다 — resolve 가능한 ref가 없는 주장은
    근거 없는(ungrounded) 산문이고, 로더는 그것을 세탁해 넣는 대신 요약 전체를 거부한다.
    """

    text: str
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"text": self.text, "source_refs": list(self.source_refs)}

    @classmethod
    def from_dict(cls, data: dict) -> "SummaryClaim":
        return cls(
            text=data["text"],
            source_refs=tuple(data.get("source_refs", ())),
        )


@dataclass(frozen=True)
class Summary:
    """한 코드 엔티티에 대한, 외부에서 생성된 의미 요약.

    palimpsest은 LLM을 절대 호출하지 않는다: 요약은 다른 곳에서 생성되어 근거결박된,
    멱등(idempotent) 적재를 위해 넘겨받는다. ``target_id``는 요약 대상 노드다
    (``SUMMARIZES`` 앵커). ``source_commit``은 요약이 생성된 기준 코드 커밋이다 —
    ``generator``/``model``과 함께, 결정론적이고 네임스페이스로 격리된 Summary id를
    도출한다.

    ``code_bound_at``은 의도적으로 여기 필드가 아니다: 신선도(freshness)는 생성기의
    벽시계(wall-clock)가 아니라 코드를 따라야 하므로, 로더가 resolve된 대상 노드의
    ``committed_at``에 결박한다(git 없는 외부 요약은 자체적으로 의미 있는 커밋 시간이
    없다). ``created_at``은 외부 생성 시각이다.
    """

    target_id: str
    claims: tuple[SummaryClaim, ...]
    generator: str
    model: str
    source_commit: str
    created_at: str
    prompt: Optional[str] = None
    confidence: Optional[float] = None
    # 외부(EXTERNAL) 심판의 의미 판정(예: ditto)이며, 생성기의 자기 보고가 아니다 —
    # 자체 필드에 두고 ``confidence``를 절대 덮어쓰지 않는다.
    # ``{"verdict": "faithful"|"unfaithful"|"unverified", "judge": str, "model": str}``.
    # palimpsest은 이것을 절대 생성하지 않고 적재만 한다. 없으면 -> None(미검증).
    semantic_verdict: Optional[dict] = None
    # 요약에 대한 외부(EXTERNAL) 임베딩이며, 다른 곳에서 생성되어 넘겨받는다
    # (provider-free: palimpsest은 절대 생성하지 않는다). ``embedding``은 벡터
    # (float[EMBEDDING_DIM]), ``embedding_model``은 그것을 생성한 모델 이름이다
    # (코사인 인덱스는 단일 모델 — 차원이 같아도 모델을 섞는 것은 무의미하다).
    # ``embedding_dim``은 선언된 차원이다. 모두 없으면 -> None이라, 임베딩 없는 기존
    # 페이로드도 변함없이 적재된다.
    embedding: Optional[list[float]] = None
    embedding_model: Optional[str] = None
    embedding_dim: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "claims": [c.to_dict() for c in self.claims],
            "generator": self.generator,
            "model": self.model,
            "source_commit": self.source_commit,
            "created_at": self.created_at,
            "prompt": self.prompt,
            "confidence": self.confidence,
            "semantic_verdict": self.semantic_verdict,
            "embedding": list(self.embedding) if self.embedding is not None else None,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Summary":
        embedding = data.get("embedding")
        return cls(
            target_id=data["target_id"],
            claims=tuple(SummaryClaim.from_dict(c) for c in data.get("claims", ())),
            generator=data["generator"],
            model=data["model"],
            source_commit=data["source_commit"],
            created_at=data["created_at"],
            prompt=data.get("prompt"),
            confidence=data.get("confidence"),
            semantic_verdict=data.get("semantic_verdict"),
            embedding=list(embedding) if embedding is not None else None,
            embedding_model=data.get("embedding_model"),
            embedding_dim=data.get("embedding_dim"),
        )


@dataclass(frozen=True)
class Risk:
    """하나 이상의 코드 엔티티에 대한, 외부에서 생성된 위험 판단.

    palimpsest은 LLM을 절대 호출하지 않는다: 판단("이 코드는 위험하다")은 다른 곳에서
    생성되어 근거결박된, 멱등(idempotent) 적재를 위해 넘겨받는다. ``flags``는 이 위험이
    플래그하는 코드 노드 id들이다(``RISKS`` 대상). 각각이 실제 그래프 노드로 resolve되어야
    하고, Risk는 반드시 >=1개를 플래그해야 한다 — resolve 가능한 플래그가 없는 위험은
    떠다니는 판단이고, 로더는 그것을 세탁해 넣는 대신 거부한다.

    :class:`Summary`처럼 ``code_bound_at``은 의도적으로 필드가 아니다: 신선도는 코드를
    따라야 하므로 로더가 플래그된 노드의 ``committed_at``에 결박한다. ``author``도 마찬가지로
    없다 — 저작자성(authorship)은 근거결박된 결정론적 코드 노드에 살고, 판단의 출처는
    ``generator``/``model``로 귀속된다. ``created_at``은 외부 생성 시각이다.
    """

    title: str
    flags: tuple[str, ...]
    generator: str
    model: str
    source_commit: str
    created_at: str
    confidence: Optional[float] = None
    # 외부(EXTERNAL) 심판의 의미 판정이며, 생성기의 자기 보고가 아니다 — 자체 필드에 두고
    # ``confidence``를 절대 덮어쓰지 않는다(Summary와 동일). 없으면 -> None(미검증).
    # palimpsest은 이것을 절대 생성하지 않고 적재만 한다.
    semantic_verdict: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "flags": list(self.flags),
            "generator": self.generator,
            "model": self.model,
            "source_commit": self.source_commit,
            "created_at": self.created_at,
            "confidence": self.confidence,
            "semantic_verdict": self.semantic_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Risk":
        return cls(
            title=data["title"],
            flags=tuple(data.get("flags", ())),
            generator=data["generator"],
            model=data["model"],
            source_commit=data["source_commit"],
            created_at=data["created_at"],
            confidence=data.get("confidence"),
            semantic_verdict=data.get("semantic_verdict"),
        )


@dataclass(frozen=True)
class InferredRelation:
    """두 개의 기존(EXISTING) 엔티티 사이의, 외부에서 생성된 추론 관계.

    palimpsest은 LLM을 절대 호출하지 않는다: 주장("A는 B와 관계있다 / 인과적으로 관계있다 /
    충돌한다")은 다른 곳에서 생성되어 근거결박된, 멱등(idempotent) 적재를 위해 넘겨받는다.
    ``source_id``/``target_id``는 기존 그래프 노드의 id다 — 둘 다(BOTH) resolve되어야
    하고, 아니면 로더는 매달린(dangling) 엣지를 세탁해 넣는 대신 관계를 거부한다
    (entity-atomic). ``rel_type``은 :data:`INFERRED_RELATION_TYPES` 중 하나여야 한다.
    새 노드는 생성되지 않는다. 이것은 순수한 엣지다.

    :class:`Risk`처럼 ``code_bound_at``은 필드가 아니다 — 로더가 source 끝점의
    ``committed_at``에 결박하여 신선도가 코드를 따르게 한다. 주장의 출처는
    ``generator``/``model``로 귀속되고, ``created_at``은 외부 생성 시각이다.
    """

    source_id: str
    target_id: str
    rel_type: str
    generator: str
    model: str
    source_commit: str
    created_at: str
    confidence: Optional[float] = None
    semantic_verdict: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "rel_type": self.rel_type,
            "generator": self.generator,
            "model": self.model,
            "source_commit": self.source_commit,
            "created_at": self.created_at,
            "confidence": self.confidence,
            "semantic_verdict": self.semantic_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InferredRelation":
        return cls(
            source_id=data["source_id"],
            target_id=data["target_id"],
            rel_type=data["rel_type"],
            generator=data["generator"],
            model=data["model"],
            source_commit=data["source_commit"],
            created_at=data["created_at"],
            confidence=data.get("confidence"),
            semantic_verdict=data.get("semantic_verdict"),
        )


@dataclass(frozen=True)
class DesignDecision:
    """코드 및 다른 엔티티에 대한, 외부에서 생성된 설계 결정.

    palimpsest은 LLM을 절대 호출하지 않는다: 결정("이것은 설계 결정이다")은 다른 곳에서
    생성되어 근거결박된, 멱등(idempotent) 적재를 위해 넘겨받는다. 세 종류의 추론된 엣지
    대상 집합을 담는다: ``decides``(``DECIDES`` 대상 — 코드 노드 또는 다른 결정),
    ``supersedes``(``SUPERSEDES`` 대상 — 다른 DesignDecision), ``addresses_risks``
    (``ADDRESSES_RISK`` 대상 — Risk 노드). 근거결박: 결정은 반드시 >=1개의 ``DECIDES``
    대상을 가져야 하고, 모든(EVERY) 엣지 대상이 실제 그래프 노드로 resolve되어야 한다.
    아니면 로더는 떠다니는 결정을 세탁해 넣는 대신 결정 전체를 거부한다(entity-atomic).

    :class:`Risk`/:class:`Summary`처럼 ``code_bound_at``은 의도적으로 필드가 아니다:
    신선도는 코드를 따라야 하므로 로더가 결정된(decided) 코드 노드의 ``committed_at``에
    결박한다. ``author``도 마찬가지로 없다 — 결정의 출처는 ``generator``/``model``로
    귀속된다. ``created_at``은 외부 생성 시각이다.
    """

    title: str
    decides: tuple[str, ...]
    supersedes: tuple[str, ...]
    addresses_risks: tuple[str, ...]
    generator: str
    model: str
    source_commit: str
    created_at: str
    confidence: Optional[float] = None
    # 외부(EXTERNAL) 심판의 의미 판정이며, 생성기의 자기 보고가 아니다 — 자체 필드에 두고
    # ``confidence``를 절대 덮어쓰지 않는다(Risk/Summary와 동일). 없으면 -> None(미검증).
    # palimpsest은 이것을 절대 생성하지 않고 적재만 한다.
    semantic_verdict: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "decides": list(self.decides),
            "supersedes": list(self.supersedes),
            "addresses_risks": list(self.addresses_risks),
            "generator": self.generator,
            "model": self.model,
            "source_commit": self.source_commit,
            "created_at": self.created_at,
            "confidence": self.confidence,
            "semantic_verdict": self.semantic_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DesignDecision":
        return cls(
            title=data["title"],
            decides=tuple(data.get("decides", ())),
            supersedes=tuple(data.get("supersedes", ())),
            addresses_risks=tuple(data.get("addresses_risks", ())),
            generator=data["generator"],
            model=data["model"],
            source_commit=data["source_commit"],
            created_at=data["created_at"],
            confidence=data.get("confidence"),
            semantic_verdict=data.get("semantic_verdict"),
        )
