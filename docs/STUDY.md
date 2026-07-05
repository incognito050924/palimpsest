# palimpsest 코드 학습 Q&A

> 코드를 읽으며 생긴 질문과, 코드·실제 그래프 근거로 단 답변.
> 답변의 예시 값은 `tests/extract/fixtures`(Java 2파일: `CommuteController`(class) + `CommuteService`(interface))를
> 실제 Neo4j에 `ingest`해 뽑은 것이다(노드 29 / 엣지 41 landed). 근거는 `파일:라인` 또는 실제 쿼리 결과.

---

## 1. 온톨로지 — 노드 / 엣지

### Q1-1. 지금 fixtures엔 Spring MVC 실제 구현체(`~Impl.java`)가 없고 interface만 있다?
**맞다 — 의도된 최소 샘플이다.** fixtures는 딱 둘: `CommuteController`(구현 class, `extends ETBaseController`)와 `CommuteService`(**interface**). `~ServiceImpl.java`는 없다. 그래서 그래프의 `Class` 노드는 2개뿐이고, "인터페이스↔구현" 관계(예: `implements`)를 보여주는 케이스는 아직 없다.

한계 하나 더: extractor는 **Java 전용**(`extract/java.py`, tree-sitter-java)이고 **best-effort**다. 타입 완전해석을 하지 않아 `implements`/`extends`는 별도 엣지로 만들지 않고, 타입 참조는 이름 기반 `DEPENDS_ON`으로만 잡힌다(`java.py:1-6`). Impl을 추가하려면 fixtures에 `.java`를 더 넣고 재-ingest하면 그래프에 바로 반영된다(멱등).

### Q1-2. `COMMUNITY` 노드의 의미? 언제, 어떻게 활용되나?
**의미**: 코드의 **결정론적 구조 묶음** — Class들을 `DEPENDS_ON`(Class→Class)과 `CALLS`(Method→Method를 선언 Class로 lift)로 이은 **연결성분(connected component)**이다. union-find로 계산하며, 모든 Class는 정확히 하나의 Community에 배타적으로 속한다(고립 Class는 자기 혼자인 싱글턴). `kg/community.py:compute_communities`(66–111), `augment_communities`(114–131).

**언제 만들어지나**: `ingest` 파이프라인이 자동으로 만든다(추론 아님, `edge_kind=deterministic`). 실제로 fixtures를 ingest하니 **Community 1개 + 멤버 2개**(CommuteController·CommuteService)가 생겼다 — 둘이 `DEPENDS_ON`으로 이어져 한 성분이기 때문.

**어떻게 활용**: 회상에서 `recall_community`(`graphrag.py:588`)가 Community를 seed로 그 멤버 Class 전체를 `MEMBER_OF` 역방향으로 끌어온다("이 덩어리에 뭐가 묶여 있나"). 또 CommunityReport(Community를 target으로 하는 Summary)의 **멤버십-grounding** 기준이 된다 — 리포트의 근거가 그 community 멤버여야만 적재된다(`summary.py:_in_community`).

**결정론 포인트**: union-find root를 사전순 min에 붙이고 결과를 정렬해서, 멤버 순서·rebuild와 무관하게 같은 id·같은 파티션이 나온다(`community.py:101-102,111`). 그래서 Community id가 `community:d5fc6ff6...`처럼 내용 해시다.

### Q1-3. `DEPENDS_ON`과 `IMPORTS`는 끝점 유형·방향이 다른데, 활용이 다른가?
**다르다. 입도(granularity)와 파생 근거가 다르다.**

| 엣지 | 방향·끝점 | 무엇에서 파생 | 뜻 |
|---|---|---|---|
| `IMPORTS` | **File → 대상**(Class 등) | `import` 문 (`java.py:128-139`) | 이 **파일**이 끌어오는 심볼 |
| `DEPENDS_ON` | **Class → Class** | 필드·파라미터·(비-wildcard)import **타입** 참조 (`java.py:231-244`) | 이 **클래스**가 타입으로 의존하는 클래스 |

즉 IMPORTS는 *파일 단위* "무엇을 import했나", DEPENDS_ON은 *클래스 단위* "무엇을 타입으로 쓰나"다. 같은 관계가 두 입도로 나타날 수 있다 — 실제 그래프에서 `CommuteController.java —IMPORTS→ CommuteService`(File→Class)와 `CommuteController —DEPENDS_ON→ CommuteService`(Class→Class)가 **둘 다** 존재한다.

활용 측면: 둘 다 회상 순회 화이트리스트 `DEFAULT_RELATIONS`에 들어가 BFS로 함께 걷힌다(`graphrag.py:63`). 파일 영향 범위를 보려면 IMPORTS, 클래스 결합도를 보려면 DEPENDS_ON.

**주의(실측)**: import 문은 많아도 IMPORTS **엣지는 해결된 내부 대상만** 남는다. CommuteController는 `ResultUtil`, `ETBaseController`, Spring 애노테이션 등 여러 개를 import하지만, 그래프에 노드로 없는(외부) 대상은 ingest 시 끝점 미해결로 **드롭**된다(`ingest.py:264-271`의 `id_to_label` 필터). 그래서 landed IMPORTS는 `CommuteService` 하나뿐이었다. → "import 문 수 ≠ IMPORTS 엣지 수"는 버그가 아니라 **소스-온리 파서가 외부 심볼을 유령 노드로 만들지 않는다**는 불변식이다.

### Q1-4. `MODIFIES`는 왜 지금 샘플에 없나?
**단일 `ingest`는 `MODIFIES`를 만들지 않기 때문이다 — 그건 `backfill`(전 이력 replay)의 몫이다.**

- `ingest`는 pin된 한 커밋의 트리를 적재하며 그 커밋의 `Episode` 노드는 만든다(실측: Episode 1개 존재). 하지만 "이 커밋이 어떤 파일을 바꿨나"는 계산하지 않는다.
- `MODIFIES`(Episode→File)는 `backfill`이 커밋마다 `changed_paths`(git diff-tree)로 변경 파일을 구해 `ingest_modifies`로 결박한다(`backfill.py:100-113`, `kg/ingest.py:136`). churn/co-change 회상 채널은 이 `MODIFIES` 스파인 위에서 돈다.
- 즉 지금 그래프엔 `Episode`는 있어도 `MODIFIES` 엣지가 0이라, `recall_churn`/`recall_cochange`는 빈 GAP을 반환한다(정상).

보고 싶으면 `backfill`로 채울 수 있다(단 fixtures는 palimpsest repo 안이라 palimpsest 전체 이력을 replay하게 된다).

### Q1-5. 각 노드/엣지 속성 설명 + 예시 값
실제 ingest된 그래프에서 뽑은 값이다.

**모든 노드·엣지 공통 provenance/freshness 속성** (`ir.py:117-130`, `_node_row`/`_edge_row`):

| 속성 | 뜻 | 예시 값 |
|---|---|---|
| `source_commit` | 이 사실을 읽어온 git 커밋(%H) | `a6d9007...96f1f6d` |
| `author` | 커밋 작성자(`%an <%ae>`) — **노드엔 저장, 회상엔 미노출**(PII) | `hskim <hskim@ecoletree.com>` |
| `committed_at` | 커밋 시각(%cI) | `2026-07-04T16:15:28+09:00` |
| `code_bound_at` | freshness 앵커(코드 노드는 committed_at과 같음) | `2026-07-04T16:15:28+09:00` |

**구조 노드(Repo/Package/File/Class/Method)** (`ir.py:133-163`):

| 속성 | 뜻 | 예시 (Method) |
|---|---|---|
| `id` = `qualified_name` | 노드 정체성. bare 평면이면 qn 그대로 | `...CommuteController#commute(HttpServletRequest)` |
| `name` | 단순명 | `commute` |
| `path` | repo-상대 파일 경로 | `src/main/java/.../CommuteController.java` |
| `start_line`/`end_line` | 1-based 소스 범위 | `46` / `54` |

- **Method의 qn엔 파라미터 타입 시그니처가 포함**된다(`#commute(HttpServletRequest)`) — 오버로드 구분용(`java.py:189`).
- **File 노드**: `qualified_name` = 경로 자체, `name` = 파일명(`CommuteController.java`), `start_line=1`.
- **`branch` 속성**(`ir.py:150-152`): 지금 예시엔 **없다**(bare 평면, `branch=None`). `reconcile`로 named 브랜치를 캡처하면 그때 `branch:<b>\x1f<qn>` 형태로 id에 접히고 `branch` 속성이 붙는다.

**추상 노드 예외**:
- **Community**: `path`/`start_line`/`end_line`이 **없다**(코드 span이 아닌 계산된 묶음). `id`=`qualified_name`=`community:<내용해시>`, 예: `community:d5fc6ff6...`.
- **Episode**: `id`=`name`=`qualified_name`= **커밋 SHA**. `author`/`committed_at`은 있고 `path`/라인은 없다.

**엣지 속성** (예: `DEPENDS_ON`, `CONTAINS`):

| 속성 | 예시 |
|---|---|
| `edge_kind` | `deterministic` (구조 엣지 전부) / `inferred`(의미층 엣지) |
| `source_commit`/`author`/`committed_at`/`code_bound_at` | 노드와 동일 형식 |

---

## 2. IR 클래스의 범위 & `scope_to_branch`

### Q2-1. `IR`의 범위는? 특정 브랜치의 "변경분"인가, 코드 저장소 "전체"인가?
**한 시점 트리의 전체 스냅샷이다 — 변경분(diff)이 아니다.**

- `extract(root)`는 `root` 아래 **모든 `.java`를 훑어** Node/Edge 리스트를 담은 **하나의 `IR`**을 만든다(`extract/java.py:273-324`). 이건 "그 순간 그 트리에 있는 코드 전체"이지 어떤 커밋 대비 변경분이 아니다.
- `IR` 클래스 자체(`ir.py:189-219`)는 그냥 `nodes + edges` 컨테이너 + 조회 헬퍼다. "브랜치"나 "변경분" 개념을 안 갖는다 — 그건 상위(extract가 무엇을 먹였나)가 정한다.
- 단일 `ingest`에선 IR = **HEAD 트리 전체**. `backfill`에선 **커밋마다 그 커밋의 트리 전체**로 IR을 하나씩 만든다(materialize→extract→ingest, `backfill.py:77-118`).

변경분을 담는 건 IR이 아니라 **`MODIFIES` 엣지**다(Q1-4). 노드 집합은 항상 "그 트리 전체", MODIFIES만 "이 커밋이 바꾼 파일"을 표시한다.

### Q2-2. 브랜치를 KG에 추가하면 그 브랜치의 "모든 코드"가 들어오나 — 공통 조상이 있어도?
**그렇다. 각 브랜치 평면은 그 브랜치에서 도달 가능한 코드 트리 전체를 담는다(공통 조상 코드 포함).** diff만 넣지 않는다.

`reconcile.capture`(`reconcile.py:161-217`)의 동작:
1. 대상 브랜치들의 커밋을 **합집합(union)**으로 모은다(각 커밋 1회, `_rev_list_union` :117-124). 공통 조상 커밋은 여기서 한 번만 등장.
2. 각 커밋 트리를 **1회만** materialize + extract(중복 추출 안 함).
3. 그 커밋을 포함하는 **모든 브랜치 평면으로 fan-out** — `scope_to_branch`로 브랜치 스코프를 씌워 각 평면에 ingest(`:197-206`, `_membership` :127-136).

결과: 브랜치 A와 B가 공통 조상을 공유해도, **A 평면엔 A 트리 전체가, B 평면엔 B 트리 전체가** 각각 scoped 노드로 존재한다. 공통 조상 커밋의 코드는 "한 번 추출"되지만 A·B 평면 양쪽에 scoped 사본으로 들어간다. 브랜치 안에서는 oldest→newest replay + MERGE-last-write-wins라 최종 노드 상태가 **그 브랜치 tip 트리**를 반영한다.

**핵심 불변식**: EXTRACT 축은 dedup(트리 1회 추출), HISTORY 축은 절대 dedup 안 함(전 브랜치 풀 히스토리 보존, merge-base 절단 거부) — `reconcile.py:9-25`. 그래서 "공통 조상이 있으니 diff만 넣자"가 아니라, 각 브랜치가 **자기 코드 전체를 독립 평면으로** 갖는다. 이게 "개인↔팀 컨텍스트 차이를 collapse 없이 표현"하는 방식이다.

### Q2-3. `scope_to_branch`가 IR의 모든 nodes/edges 브랜치를 바꾸는데, IR 범위는?
Q2-1의 답이 곧 답이다 — **IR = 트리 전체 스냅샷**이므로, `scope_to_branch(ir, branch)`가 그 IR의 **모든** 노드/엣지 id에 브랜치 차원을 접는 건 "이 트리 전체를 이 브랜치 평면에 속하게 만든다"는 뜻이다(`ir.py:222-241`).

- 순수 transform이다: 원본 IR을 **변경하지 않고** 새 IR을 반환한다(`ir.py:229-230`). 그래서 같은 base IR(한 커밋 트리)을 여러 브랜치로 fan-out할 수 있다(Q2-2의 3단계).
- 노드 id와 엣지 끝점이 **같은 `branch_scoped_id` 함수**를 쓰기 때문에(`ir.py:32-43`), 전체를 한꺼번에 scope해야 내부 참조가 깨지지 않는다. 일부만 scope하면 엣지 끝점이 노드 id와 안 맞아 유령이 생긴다 — 그래서 "모든 nodes/edges"를 바꾸는 게 정상이다.
- `branch=None`이면 bare qn 그대로(단일 브랜치·backfill과 byte-identical, 가산적). named면 `branch:<b>\x1f<qn>`.

---

## 3. 추론(inferred) 의미 계층 — Summary / Risk / DesignDecision / 관계

구조 계층(코드 파싱 → Class/Method/CALLS…)은 "코드가 이렇게 생겼다"는 **사실**이다.
그 위에 얹는 이 계층은 **"코드에 대해 외부가 내린 판단"**이다. palimpsest는 이걸 **안 만든다**(provider-free) —
외부 생성기(LLM/에이전트)가 payload로 만들어 넘기면 **근거결박해서 적재만** 한다. 정의: `ir.py:70-106`.

### Q3-1. `SUMMARY` / `SUMMARIZES` — "이 코드가 무엇을 하는가"
- **Summary**(노드) = 외부가 쓴 요약. `Summary ─SUMMARIZES→ 코드노드` (`ir.py:74-75`).
- 예: "CommuteController는 출퇴근 기록 CRUD 담당" → 그 Class를 SUMMARIZES.
- claim마다 근거(`source_refs`) 필수. 임베딩 있으면 semantic KNN 검색 대상.
- **CommunityReport** = target이 Community인 Summary(§1의 커뮤니티 요약이 바로 이것).

### Q3-2. `RISK` / `RISKS` — "이 코드에 이런 위험이 있다"
- **Risk**(노드) = 외부가 매긴 위험 판단. `Risk ─RISKS→ 코드노드(들)` (`ir.py:81-82`).
- `flags` = 위험이 가리키는 코드, `title` = 위험 내용.
- 예: "이 메서드는 SQL 인젝션 위험" → 그 Method를 RISKS.

### Q3-3. `DESIGN_DECISION` + `DECIDES` / `SUPERSEDES` / `ADDRESSES_RISK` — 설계 결정 계보
**DesignDecision**(노드) = "이렇게 설계하기로 했다"는 결정 기록(ADR 비슷). 세 엣지로 엮인다 (`ir.py:88-91`):

| 엣지 | 방향 | 뜻 |
|---|---|---|
| `DECIDES` | Decision → 코드(또는 다른 Decision) | "이 결정이 이 코드를 규율한다" |
| `SUPERSEDES` | Decision → 이전 Decision | "이 결정이 그걸 **대체**한다" (bi-temporal: 이전 것 `valid_to` 세팅, 삭제 안 하고 전이력 보존) |
| `ADDRESSES_RISK` | Decision → Risk | "이 결정이 그 위험에 **대응**한다" |

→ **결정↔코드↔위험↔이전결정**을 잇는 계보 그래프. "왜 이렇게 짰지?"를 회상 가능.

### Q3-4. `CAUSALLY_RELATES` / `RELATES_TO` / `CONFLICTS_WITH` + `INFERRED_RELATION_TYPES` — 기존 노드 둘 사이 일반 관계
위 셋과 달리 **새 노드를 안 만들고 이미 있는 노드 둘을 잇는 순수 엣지**다 (`ir.py:93-100`).

| 상수 | 뜻 |
|---|---|
| `RELATES_TO` | 일반 연관(association) |
| `CAUSALLY_RELATES` | **방향성 인과** (cause → effect) |
| `CONFLICTS_WITH` | 충돌 — 숨은 의도 상충 표시 |
| `INFERRED_RELATION_TYPES` | 위 셋의 **닫힌 집합**(frozenset, `ir.py:100`) |

`INFERRED_RELATION_TYPES`의 역할: rel_type이 이 셋 **밖이면 로더가 거부**한다. 자유 형식 문자열이 쿼리에 못 들어가게(주입 방지) + 아무 관계나 못 만들게(온톨로지 통제)하는 화이트리스트.

### Q3-5. 왜 이것들이 다 한 묶음인가 (공통 규칙)
- **`edge_kind='inferred'`** — 구조층(`deterministic`)과 스키마로 강제되는 **세탁 금지(no-laundering) 분리** (`ir.py:102-106`).
- **근거결박**: 모든 참조가 실제 코드 노드로 resolve돼야 하고, 하나라도 안 풀리면 **엔티티 통째 거부**(헛소리 세탁 방지).
- **freshness = 코드 추종**(`code_bound_at`은 결박된 코드의 `committed_at`), `semantic_verdict`(외부 judge)는 생성기 `confidence`와 별개.
- 로더: `kg/summary.py`·`risk.py`·`decision.py`·`relation.py`. 회상: `recall_semantic`·`recall_risk`·`recall_decision`·`recall_community` + relations 채널(`graphrag.py`).

### Q3-6. 지금은 왜 비어 있나
이 계층은 **외부 생산자(LLM)가 있어야 채워진다**(Community report와 같은 이유). 그래서 라이브 그래프엔 Summary/Risk/DesignDecision이 하나도 없고, `query` 실행 때 뜬 `SUMMARIZES/RISKS/DECIDES 라벨 없음`은 버그가 아니라 "아직 생성물 미적재"다. 로더·회상 채널은 준비돼 있어 payload만 `load`하면 채워진다.

---

## 부록 — 이 문서의 예시를 직접 재현하기
```bash
docker run -d --name palimpsest-neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/palimpsest neo4j:5-community
export NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=palimpsest
.venv/bin/python -m palimpsest ingest --repo tests/extract/fixtures --commit HEAD
# 브라우저: http://localhost:7474  (neo4j / palimpsest)
#   MATCH (n)-[r]->(m) RETURN n,r,m;
```
