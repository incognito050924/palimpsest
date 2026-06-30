# 연구보고서 — Meta pre-compute · HugRAG · Knowledge Graph (palimpsest 접목 관점)

- 목적: palimpsest 기획/설계 보강 입력. 3개념 + "Neo4j에서 코드 정적분석 ∪ semantic 데이터를 어떤 node/edge로 설계할지" 종합.
- 방법: 5각도 fan-out 검색 → 22개 1차 자료 fetch → 107개 주장 추출 → 상위 25개 3표 적대적 검증(2/3 refute 시 기각).
- 검증 결과: **23 confirmed · 2 refuted · 0 unverified.**
- 작성: deep-research 워크플로(104 에이전트). 날짜 2026-06-30.
- 위상: **조사 산출물(연구 리포트)**. 결정이 아니라 설계 보강의 근거 입력. 권위는 코드·ADR이지 이 문서가 아니다.

---

## 0. 한 줄 결론

네 축 모두 1차 자료로 확인됐고, **ADR-20260626(KG 본체 + GraphRAG 회상 + 전이력 보존 + 자동 캡처)과 충돌하는 근거는 없었다** — 오히려 각 시스템이 우리 모델의 개별 조각을 이미 실증한다. Neo4j 설계는 **「CPG의 type-label overlay + HugRAG의 unified edge space + Graphiti의 bi-temporal/Episode 속성」** 결합으로 귀결되며, 결정론적 구조 엣지와 LLM 추론 엣지를 `edge_kind` 속성 + 별도 label로 분리하고 provenance·2축 신선도를 **엣지 속성**으로 부착한다.

---

## 1. Meta pre-compute — Glean (+ CodeCompose)

### Glean (facebookincubator, OSS) — 우리 projection 모델의 직접 선례 [confidence: high, 3-0]

- **무엇을 미리 계산하나**: 소스코드 정보를 **predicate(≈SQL 테이블)의 인스턴스인 fact(≈행)**로 사전 색인. 심볼별 위치·타입·관계, cross-reference(함수/메서드 호출), call/type hierarchy. derived fact는 질의시점 또는 ahead-of-time 파생.
- **무엇을 질의하나**: **Angle** — Datalog 계열 선언형 질의어. 스키마와 질의를 같은 언어로 기술. 즉 **"재파싱이 아니라 색인된 사실을 질의"**.
- **엣지도 1급 fact** [3-0]: `Class{name,line}`(≈노드), `Parent{child:Class, parent:Class}`(상속 엣지), `Has{class, member, access:public|private}`(멤버 + 수식어). 관계 자체가 **속성을 가진** 1급 fact.
- **증분 색인** [2-1, verifier high]: immutable DB를 **stacking** — 각 층이 하위 층 fact를 비파괴적으로 추가/은폐. 색인 비용을 O(repository)가 아니라 **O(changes)**.
- 출처: https://engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/ · https://github.com/facebookincubator/Glean · https://glean.software/docs/angle/guide/

**palimpsest 접목**: ADR-20260626의 "git=SoT, 그래프=재구축 가능한 projection, 미리 계산해 질의"를 **그대로 실증**. predicate-스키마/fact 분리 = 우리 Neo4j projection 노드/엣지의 직접 모델. derived-fact 개념 = LLM 추출 의미 엣지를 'derived' 층으로 다루는 근거. stacking 증분 = git commit별 증분 projection + **삭제 대신 invalidate(전이력 보존)**의 검증된 선례. **충돌 없음.**
**주의**: Glean 백엔드는 RocksDB+Angle이지 Neo4j가 아니다 — **'구현'이 아니라 '모델'을 차용.**

### CodeCompose (Meta 사내 AI 코드 어시스턴트) [confidence: high, 3-0]

- InCoder LLM 기반, 커서 양쪽 컨텍스트를 쓰는 bi-directionality + 생성. 사내 수만 개발자·9개 언어.
- 출처: https://arxiv.org/abs/2305.12050
- **palimpsest 접목**: '컨텍스트 사전 활용' 사례이긴 하나 fact 그래프를 미리 계산해 질의하는 모델은 아니다 — 축(1)의 **약한 사례**로만 의미.

---

## 2. HugRAG (arXiv 2602.05143) — 정적∪생성형 엣지 공존의 직접 선례

> **식별자 주의**: arXiv 2602.05143 제목은 "HugRAG"이나 본문 프레임워크명이 여러 fetch에서 **"CausalRAG2"**로 렌더됨(동일 논문·저자·abstract, v2에서 ICML 2026 보고). 인용 시 둘 병기.

- **Unified edge space** [3-0]: 검색을 `ℰuni = Estruc ∪ Ehier ∪ 𝒢c` 위 **priority-based traversal**로 모델링. Structural(국소 컨텍스트) + Hierarchical(수직 drill-down) + Causal Gates(모듈 간 추론)를 **하나의 공간**으로 통합 → 모듈 간 information isolation 해소.
- **Hierarchical causal gating** [3-0]: topologically distant하지만 logically related 가능한 **모듈 쌍 후보** 선정 → 그 summary에 대해 **LLM이 causal connection plausibility 평가** → 통과 쌍만 **undirected·binary shortcut 엣지(causal gate)** 추가. 이건 구조 엣지와 구별되는 **명시적 생성형/추론 엣지**이고 **confidence threshold(score≥τ)**로 게이팅.
- **spurious 억제** [2-1, medium]: subgraph를 token-efficient table로 linearize + spurious-aware 프롬프트로 'causal support vs spurious correlation' 명시 구분. (단, 독립 2단계 필터가 아니라 한 LLM 호출의 두 측면. headline 메커니즘은 causal gating.)
- **겨냥한 gap** [3-0]: 기존 graph-RAG는 entity-centric node matching 과의존 + explicit causal modeling 부재 → unfaithful/spurious. 기존 causality-aware는 local/single-doc 한정 + modular graph의 information isolation.
- 출처: https://arxiv.org/html/2602.05143v2 · https://arxiv.org/html/2602.05143v1 · https://arxiv.org/abs/2602.05143

**palimpsest 접목**: '구조 엣지 + 계층 엣지 + (생성형)인과 엣지를 한 그래프에서 우선순위 순회' = 우리 **'정적 엣지 ∪ semantic 엣지 단일 그래프'의 직접 선례**. confidence-threshold 게이팅 = 추론 엣지에 confidence 속성을 다는 설계 뒷받침. cross-module reasoning = 우리 cross-branch design-risk와 동형. **충돌 없음.**

> ⚠ **기각된 주장(설계 직접 영향)** [1-2 refuted]: "HugRAG의 세 엣지 클래스가 deterministic vs LLM-inferred로 정확히 1:1 대응한다"는 **강한 해석은 거짓**. 실제는 **structural+hierarchical(둘 다 결정론) + causal-gate(추론) = 2:1**. → 우리 static/generative 분리는 HugRAG에서 **아이디어만 빌리고 매핑은 직접 설계**해야 한다.
> ⚠ HugRAG **성능/벤치마크 수치는 검증 통과한 게 없음** — 살아남은 건 전부 메커니즘 수준. 효과성은 독립 검증 안 됨(논문 자기보고).

---

## 3. Knowledge Graph for code — CPG/Joern (+ Graphiti)

### Code Property Graph (Joern) — 우리 정적층 온톨로지의 출발점 [confidence: high, 3-0]

- **형식**: language-agnostic 중간 표현. 형식적으로 **directed, edge-labeled, attributed multigraph** — 노드는 타입 가진 program construct + k-v 속성, 라벨 붙은 방향 엣지, 같은 노드 쌍 간 다중 엣지 허용. → **Neo4j property graph 정의와 일치.**
- **overlay 통합**: AST+CFG+PDG를 **층 쌓기**로 한 스키마에. AST 노드 → 부분집합을 CFG_NODE 표시 + CFG 엣지 → PDG(=DDG+CDG)는 REACHING_DEF·CDG 엣지. 단일 구조가 syntax·control-flow·intra-procedural data-flow 보유.
- **엣지 타입(명명)**: `AST, CFG, CALL, ARGUMENT, RECEIVER, DOMINATE, POST_DOMINATE, CDG, REACHING_DEF(=데이터흐름), CONTAINS, REF, BINDS`.
- **노드 범위**: 저수준 구문(method·variable·control)부터 **고수준(HTTP endpoint, finding)까지** 한 그래프에. → **'코드 정적분석 ∪ semantic' 단일 그래프의 직접 실증.**
- 출처: https://cpg.joern.io/ · https://docs.joern.io/code-property-graph/

**palimpsest 접목**: CPG = 코드 정적분석을 property graph로 담는 **검증된 온톨로지** → 우리 정적층 노드/엣지의 직접 출발점. **overlay 패턴**(같은 노드 집합 위 엣지 type label로 여러 층) 차용 → 그 위에 **의미층(summary/decision/risk) 엣지를 추가 overlay**. finding/endpoint가 syntax 노드와 공존 = 우리 DesignDecision·Risk·Summary 노드를 정적 노드와 같은 그래프에 두는 설계가 CPG 전통과 일관.

### Graphiti (getzep) — 우리 2축 신선도·전이력·회상의 참조 구현 [confidence: high, 3-0]

- **bi-temporal**: 모든 fact(엣지)가 validity window(언제 참됨 / 언제 superseded). 모순 입력 시 기존 fact **삭제 아니라 invalidate**. '지금 참' + '과거 시점 참' 모두 질의. (Zep 논문이 valid/invalid + created/expired 4 타임스탬프 명세.)
- **Episode provenance**: 원시 ingest 데이터를 Episode(ground-truth stream)로, **파생 fact가 전부 source episode로 역추적**. provenance가 1급 패턴.
- **하이브리드 검색**: semantic embedding + keyword BM25 + graph traversal을 **단일 경로**로 결합, sub-second(vendor 자기보고).
- 출처: https://github.com/getzep/graphiti · https://arxiv.org/abs/2501.13956

**palimpsest 접목**: 우리 **2축 신선도 + 전이력 보존**의 거의 그대로의 참조 — valid-time↔결정-계보 신선도, '삭제 대신 invalidate'↔전이력 보존, Episode↔git commit(=SoT 자연 정합), 하이브리드 검색↔ADR-20260626의 GraphRAG 회상층 구현 패턴. **충돌 없음.**

---

## 4. ★ 핵심 산출물 — Neo4j node/edge 설계 인사이트 [confidence: medium · 제안/종합]

> 이 절은 **검증된 1차 사실의 종합·추론**이지 단일 출처 사실이 아니다. 노드/엣지·속성 **이름은 제안**이며 v1 스파이크로 검증 대상. 근거 조립: CPG가 syntax∪semantic을 한 그래프에(§3) + 엣지 type label로 층 구분, HugRAG가 structural∪hierarchical∪inferred-causal을 unified edge space로(§2), Glean이 노드·엣지를 모두 1급 fact로(§1), Graphiti가 bi-temporal+Episode를 엣지 단위로(§3).

### 4.1 제안 노드 타입

| 층 | 노드 타입 | 출처 근거 |
|---|---|---|
| **정적층** (CPG 그대로) | `Repo` · `Module/File` · `Type/Class` · `Method/Function` · `Variable/Local` · `CallSite` | CPG 노드 온톨로지 |
| **의미층** (overlay 추가) | `Summary/CommunityReport` · `DesignDecision/ADR` · `Risk/Finding` · `Episode/SourceCommit` | CPG finding/endpoint 선례 + Graphiti Episode + GraphRAG 커뮤니티 노드 |

### 4.2 제안 관계 타입 — 결정론 vs 생성형 분리

| 부류 | 엣지 타입 | edge_kind |
|---|---|---|
| **결정론적 구조 엣지** (CPG 그대로, git projection으로 재생성) | `CONTAINS` · `CALLS` · `REACHING_DEF`(DATAFLOW) · `IMPORTS` · `INHERITS` · `REF` | `deterministic` |
| **생성형 추론 엣지** (LLM 생성, 게이팅) | `SUMMARIZES` · `CAUSALLY_RELATES` · `ADDRESSES_RISK` · `DECIDES` · `RELATES_TO` | `inferred` |

- **분리 표시 2중**: ① 별도 edge **label**(type만으로 자연 분리) + ② 모든 엣지에 `edge_kind = deterministic|inferred` 속성. → "정적∪semantic을 한 그래프에 두되 출처·신뢰를 혼동하지 않는다"(세탁 금지)를 스키마 레벨에서 강제.

### 4.3 provenance·2축 신선도 속성 부착 (Graphiti 패턴, **엣지 속성으로**)

모든 엣지에:
- `source` = Episode/commit SHA 역참조 → **provenance**
- `valid_from` / `valid_to` = 채택·기각·중단 전이를 **삭제 대신 invalidate** → **결정-계보 신선도** (전이력 보존)
- `code_bound_at` = 연결된 코드 심볼의 마지막 git 변경 시점 → **코드-결박 신선도**
- `edge_kind = deterministic|inferred`; inferred일 때 추가로 `generator` / `model` / `confidence` / `created_at`

→ 정적 엣지는 git projection 재생성으로 `code_bound_at` 자동 갱신, 추론 엣지는 LLM 생성 시점·confidence 보존. 우리 2축 신선도가 `code_bound_at`(코드-결박) ↔ `valid_from/valid_to`(결정-계보)로 깔끔히 분리 매핑되고 ADR-20260626 전이력 보존과 정합. **충돌 없음.**

---

## 5. 검증 한계·caveat (정직하게)

1. **HugRAG 식별자 불일치** — HugRAG=CausalRAG2 동일 논문(제목 vs 본문명). 인용 시 병기.
2. **HugRAG 성능 미검증** — 메커니즘 수준만 신뢰. 효과성 독립 검증 없음.
3. **기각 #1 (설계 직접 영향)** — "세 엣지 클래스 = static/generative 1:1"은 거짓(2:1). 매핑은 직접 설계.
4. **기각 #2** — "CausalRAG2가 causal gating으로 spurious 억제 + 대규모 추론"이라는 포괄 주장 1-2 기각(메커니즘 개별 주장은 통과, 효과 일반화는 미검증).
5. **Graphiti sub-second latency = vendor 자기보고.**
6. **§4 설계 인사이트는 제안(medium)** — 이름·구조는 v1 스파이크로 검증.
7. **모델/패턴 차용 ≠ 구현 차용** — Glean(RocksDB)·Joern(자체 store)·Graphiti(Neo4j 등)는 백엔드가 다름. Neo4j 이식 비용은 조사 범위 밖.
8. **현재성**: 전 자료 2024–2026(Glean 2024-12, HugRAG 2026-02, Graphiti/Zep 2025–2026).

---

## 6. 미결 질문 (다음 스파이크/결정 대상)

1. **derived 엣지 생성 메커니즘**: Neo4j 위에 Glean식 Datalog 파생 층 vs 순수 Cypher+앱코드? (Angle 파생은 Neo4j 직접 이식 안 됨.)
2. **추론 엣지 precision 가드레일**: HugRAG의 confidence threshold·spurious-aware 프롬프트는 검증 수치가 없음 → 거짓 인과/거짓 design-risk 엣지 폭증 억제책을 직접 설계·측정.
3. **intra-procedural → cross-branch 확장**: CPG 데이터흐름은 절차 내부 한정. HugRAG cross-module gate가 그 간극 후보지만 결합은 미검증 스파이크 대상.
4. **코드-결박 신선도 정책**: 갱신 단위(심볼/파일/커밋) + 추론 엣지 stale 판정(연결 코드 변경 시 LLM 추론 미갱신) 트리거. Graphiti는 valid-time만, code-bound 축은 우리 고유 → 선례 없음.

---

## 부록 — 1차 출처

- Glean: engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/ · github.com/facebookincubator/Glean · glean.software/docs/angle/guide/
- CodeCompose: arxiv.org/abs/2305.12050
- HugRAG/CausalRAG2: arxiv.org/html/2602.05143v2 · /v1 · arxiv.org/abs/2602.05143
- CPG/Joern: cpg.joern.io · docs.joern.io/code-property-graph/
- Graphiti/Zep: github.com/getzep/graphiti · arxiv.org/abs/2501.13956
- neo4j-graphrag: neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html
