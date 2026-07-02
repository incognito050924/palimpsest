# ADR-20260702-community-deterministic-structural — Community 멤버십 = 결정론적 구조 분할(inferred 아님), CommunityReport prose는 유예

- 식별자: `ADR-20260702-community-deterministic-structural` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-02
- work item: wi_2607010n6 (Community 노드 타입 — 결정론적 구조 그룹핑)
- 관계: `ADR-20260701-v1-ontology-recall-reframe`를 **구체화(refine)**한다 — supersede 아님. 그 ADR §결정3이 유예한 것은 **생성형 community *report*(GraphRAG 요약 prose)**이고, 그것은 계속 유예된다. 같은 ADR line 25("향후 inferred/deterministic writer는 분리 규약을 지켜야 한다")가 새 결정론 writer를 선인가했다. `ADR-20260701-v1-ontology-recall-reframe` line 24가 `Community`를 "GraphRAG 요약 노드"로 묶어 inferred 의미층으로 분류했던 것을 이 ADR이 **탐지(멤버십)와 생성(report)으로 분리**해 정정한다.

## 맥락

`ADR-20260701-v1-ontology-recall-reframe` §결정3은 "생성형 LLM 합성·semantic 요약(GraphRAG community report)·inferred 엣지"를 한 덩어리로 유예했고, 같은 ADR의 온톨로지 초안(line 24)은 `Community`를 "GraphRAG 요약 노드"로 적어 inferred 의미층에 넣었다. 그러나 GraphRAG에서 community는 두 가지 다른 것이다: ① **탐지(detection)** — 그래프를 연결 요소로 분할하는 결정론적 그래프 알고리즘(LLM·GDS 불필요) · ② **생성(report)** — 각 community에 LLM이 붙이는 요약 prose(inferred). ①은 결정론이고 ②만 생성형이다. 이 둘을 한 덩어리로 유예하면 결정론적 구조 그룹핑까지 불필요하게 막힌다.

## 결정

1. **Community 멤버십 = 결정론적 구조 분할.** Class 수준 무방향 연결 요소(union-find)로 계산한다 — 두 Class는 RESOLVED 결정론 IR에서 교차-클래스 엣지(CALLS를 Class로 승격 / DEPENDS_ON)가 있으면 연결. 분할은 배타·평탄(모든 Class가 정확히 하나의 Community에 속함). LLM 없음, GDS 없음.
2. **`edge_kind='deterministic'`.** `(:Class)-[:MEMBER_OF]->(:Community)` 멤버십 엣지는 결정론 층에 속한다 — 기존 deterministic writer 규약(deterministic⊎inferred==total ∧ NULL==0)을 그대로 따른다. inferred 아님.
3. **재구축 안정 id.** Community id = `community:` + sha256(정렬된 멤버 목록). 멤버 집합이 같으면 재계산해도 같은 id(멱등 rebuild), git=SoT projection과 정합.
4. **IR에 materialize → partition 불변식이 카운트.** Community 노드/MEMBER_OF 엣지를 IR에 얹어 partition 불변식이 이들을 포함해 검사한다.
5. **회상: `recall_community` 진입점.** 멤버 Class를 bounded·grounded·author-omitted·LLM-free로 반환한다. MEMBER_OF는 순회 화이트리스트에 없어(traversal에서 제외) 일반 순회로 걸어지지 않는다 — Community는 오직 이 전용 진입점으로만 조회된다.
6. **CommunityReport(요약 prose)는 계속 유예/범위 밖.** 각 community에 붙는 LLM 생성 요약(inferred)은 외부 LLM 생산자가 필요하며 이번 범위 밖이다. `ADR-20260701-v1-ontology-recall-reframe`이 유예한 생성형 report는 그대로 유예된다.

## 실현·검증된 사항 (code = SoT, 동작·알고리즘은 코드가 권위 — 여기 prose로 이중화하지 않음)

- 탐지·materialize: `src/palimpsest/kg/community.py` (`compute_communities` union-find, `community_id` sha256, `augment_communities`가 IR에 Community 노드·MEMBER_OF 엣지 append).
- 회상 진입점: `recall_community` in `src/palimpsest/recall/graphrag.py` (MEMBER_OF는 `DEFAULT_RELATIONS` 화이트리스트에서 의도적으로 제외).
- 테스트: `tests/kg/test_community.py` · `tests/recall/test_recall_community.py` (전체 55 passed).

## 근거 (rationale)

- community **탐지**는 결정론적 그래프 알고리즘(union-find)이라 v1 결정론 구조층에 속한다 — 유예된 **생성형 report**와 본질이 다르다. 둘을 분리하면 LLM 없이도 구조 그룹핑을 실현할 수 있다.
- `ADR-20260701-v1-ontology-recall-reframe` line 25가 deterministic/inferred 분리를 지키는 새 writer를 선인가했으므로, MEMBER_OF를 deterministic으로 얹는 것은 그 규약 안에 있다(사용자 확인 불필요한 method 정합).
- MEMBER_OF를 순회 화이트리스트에서 빼 Community가 일반 회상 순회를 오염시키지 않게 한다 — 전용 `recall_community` 진입점으로만 노출.

## 철회·변경 조건 (change_condition)

- **CommunityReport(생성형 요약 prose) 도입** 시 재검토한다 — 외부 LLM 생산자가 붙고 그 report를 `edge_kind='inferred'`로 적재하게 되면, 그 층은 이 ADR이 아니라 `ADR-20260701-semantic-layer-load-contract`의 적재 계약을 따른다.
- 탐지 알고리즘(무방향 연결 요소·Class 수준)은 git=SoT 재구축 projection이므로, 다른 분할(가중·방향·계층)이 필요하다는 근거가 나오면 교체 가능(코드가 권위).
