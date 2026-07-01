# 연구보고서 — Meta pre-compute · HugRAG · Knowledge Graph (palimpsest 접목 관점)

- 목적: palimpsest 기획/설계 보강 입력. 3개념 + "Neo4j에서 코드 정적분석 ∪ semantic 데이터를 어떤 node/edge로 설계할지" 종합.
- 방법: 5각도 fan-out 검색 → 22개 1차 자료 fetch → 107개 주장 추출 → 상위 25개 3표 적대적 검증(2/3 refute 시 기각).
- 검증 결과: **23 confirmed · 2 refuted · 0 unverified.**
- 작성: deep-research 워크플로(104 에이전트). 날짜 2026-06-30.
- 위상: **조사 산출물(연구 리포트)**. 결정이 아니라 설계 보강의 근거 입력. 권위는 코드·ADR이지 이 문서가 아니다.
- **확장 라운드(§7, 2026-07-01)**: "Meta의 tacit-knowledge pre-compute(five-questions)" 질문에서 출발한 2차 조사. §1~§6의 3표 적대검증과 달리 **fan-out 웹검색 + 서브에이전트 종합**이며 검증 위상이 낮다 — §7 안에서 항목별로 **자기보고 / 비전-무평가 / 실측**을 구분 표기했다.

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

## 7. Tacit-knowledge 계열 — Meta five-questions·CDT·Lore·Heidelberg·Confucius (2026-07-01 확장 라운드)

> **위상·검증 경고**: 이 절은 §1~§6과 **다른 조사 라운드**다. 방법은 6각도 fan-out 웹검색 → 3개 축(Meta 시스템·학술·상용)을 서브에이전트로 위임·종합. **§1~§6의 3표 적대검증을 거치지 않았다.** 그래서 주장마다 검증 위상을 **[자기보고] / [비전·무평가] / [실측] / [2차·의견]**으로 표기한다. 결론이 아니라 근거 입력이라는 문서 전체 위상(권위=코드·ADR)은 그대로다.
>
> **동기**: §1의 Glean은 "정적 사실 pre-compute"였는데, 사용자가 물은 Meta의 *tacit-knowledge* pre-compute는 **별개 시스템**(아래 7.1)이다. §4의 의미층 노드·추론 엣지 설계에 직접 닿는 계열이라 확장 조사했다.

### 7.1 Meta "five questions" tribal-knowledge 시스템 [자기보고 중심]

§1 Glean과 **다른 Meta 시스템**이다(Glean=fact 색인 DB / 이건 LLM 에이전트 스웜이 암묵지를 요약). 출처: engineering.fb.com/2026/04/06/developer-tools/how-meta-used-ai-to-map-tribal-knowledge-in-large-scale-data-pipelines/

- **five questions (verbatim)** — module analyst가 파일마다 답하는 5개: ① "What does this module configure?" ② "common modification patterns?" ③ "non-obvious patterns that cause **build** failures?" ④ "cross-module dependencies?" ⑤ "tribal knowledge buried in code comments?" (Meta는 ⑤가 가장 깊은 학습을 냈다고 — 숨은 명명 규칙, append-only 식별자 규칙.)
- **context 파일 = "compass, not encyclopedia"**: 25~35줄(~1,000토큰), **4개 고정 섹션** — (1) Quick Commands (2) Key Files(실제 봐야 할 3~5개) (3) Non-Obvious patterns (4) See Also. 59개 전체가 컨텍스트의 **0.1% 미만**, **opt-in 로딩**, 품질 게이팅.
- **에이전트 스웜 파이프라인(50+, 9단계)**: explorer 2 → module analyst 11(5질문) → writer 2 → critic 10+(3라운드) → fixer 4 → upgrader 8 → prompt tester 3 → gap-filler 4 → final critic 3.
- **결과** [자기보고]: 커버리지 5%→100%, 파일 ~50→4,100+(4 repo·3 언어), 비자명 패턴 0→50+, critic 품질 3.65→4.20. 헤드라인 **"태스크당 tool 호출·토큰 ~40%↓"**은 **n=6 preliminary 테스트, 독립검증 없음** — techjacksolutions도 "벤치마크가 아니라 방향 신호"로 못 박음(techjacksolutions.com/ai-brief/the-context-problem-in-enterprise-agentic-ai-what-metas-trib/). 파일경로는 zero-hallucination 검증되나 **의미(semantic) 정확성은 무보장**.
- **자가유지**: 몇 주마다 자동 job이 경로검증·커버리지갭탐지·critic 재실행·stale 참조 자동수정. *"Context that decays is worse than no context at all."* 단 새 tribal knowledge 탐지는 **미구현(future work)**.
- ⚠ **Meta 자인 반대근거(중요)**: 학술 연구에서 AI 생성 context 파일이 Django·matplotlib 같은 유명 OSS에서 **에이전트 성공률을 오히려 떨어뜨렸다**. Meta 반론은 "그 repo는 pretraining에 있고 우리 건 학습데이터에 없는 사유 config-as-code" — 자기정당화라 회의적으로 볼 것. → 우리 추론 엣지도 **오히려 해가 될 수 있는 fail-case가 실재**(§6-2 강화).
- **의미-격차 비판**(jprevanth, Medium): *"what it configures ≠ what it means"* — Meta는 무엇을 설정하는지는 문서화했지만 **비즈니스/의미**는 여전히 격차. (벤더 홍보는 discount, 논지만 취함.)

### 7.2 학술 계열 — 같은 진단, 다른 무게 [위상 혼재]

다섯 논문 전부 진단 동일: **why(설계 근거·제약·기각 대안·책임 배분)는 코드 밖·시간에 얽혀 LLM이 요청 시 복원 못 함.** 처방이 갈린다.

| 논문 | 소속 | 위상 | 그래프 | provenance | freshness | 우리에게 |
|---|---|---|---|---|---|---|
| **Code Digital Twin (CDT)** | Fudan | **비전·평가 0** | ✅ 타입드 | 1급 엣지 | change-event 증분 재추출 | ★ 온톨로지 직접 선례 |
| **Lore** | 독립 | 제안만 | ❌ commit 원자 | commit hash 결박(공짜) | 불변성=anti-drift + `stale` 플래그 | 값싼 provenance 대안 |
| **Context Engineering(AGENTS.md 연구)** | Heidelberg | **실측** | ❌ | ❌ | 실측: 50% 파일 생성 후 무수정 | 경험적 경고 |
| **Confucius Code Agent** | **Meta+Harvard** | **유일 평가**(SWE-Bench 74.6%) | ❌ Markdown 트리 | ❌ | drift 탐지 없음 | 런타임 메모≠설계 그래프 |

- **CDT [비전·평가 0]** (arxiv.org/abs/2503.07967; 근접 중복본 2510.16395는 **철회됨**) — **§4가 "HugRAG에서 아이디어만 빌리고 매핑은 직접 설계"라 미뤄둔 부분을 CDT가 구체적 2층 온톨로지로 이미 제안**. **물리층**(결정론·정적분석): `contains, defines, imports, calls, reads-writes, depends-on`. **개념층**(LLM 추론) 노드: Domain Concepts / Functionalities·Responsibilities / Rationales·Constraints. **타입드 관계 어휘**: `operationalized-by, decomposes-to, has-responsibility, assigned-to, constrained-by, justified-by`. provenance는 버전·commit·PR·issue로 1급. freshness는 change-event 증분 재추출 + link-integrity 검증. **단 구현·평가 전무 — 온톨로지는 빌리되 검증은 우리 몫.**
- **Lore [제안만]** (arxiv.org/abs/2603.15566) — 정반대 극(near-zero 인프라). commit trailer 9종(`Constraint / Rejected / Confidence / Scope-risk / Reversibility / Directive / Tested / Related`)으로 "Decision Shadow"를 commit에 원자 결박. **불변성으로 drift 원천봉쇄** — 우리 "git=SoT, Episode=commit"의 극단적 버전. cross-record 링크는 `Related:`뿐.
- **Heidelberg [실측·유일]** (arxiv.org/abs/2510.21413) — GitHub 10,000 repo 마이닝. AGENTS.md/CLAUDE.md 채택 **5%**, **관찰된 14개 정보 카테고리**(컨벤션·기여가이드·아키텍처·빌드·테스트·기술스택·트러블슈팅·패턴·보안 등 = Meta 5질문의 확장 체크리스트), 5개 서술 스타일. **파일의 50%가 생성 후 한 번도 갱신 안 됨** = "write-once context 파일은 실제로 썩는다"는 경험 증거.
- **Confucius [유일 평가]** (arxiv.org/abs/2512.10398, Meta+Harvard) — SWE-Bench-Verified 74.6%·Pro 54.3%(논문 자기보고, 미재현). 단 **정적 index·설계 그래프를 안 만든다** — planner 기반 계층 working-memory + note-taking 에이전트가 실행궤적을 Markdown 노트로 distill(성공전략+실패모드). 지식이 **행동·경험적**이지 설계-근거가 아님, **staleness 메커니즘 없음**.

### 7.3 상용 지형 [2차·문서화 능력]

| 도구 | 카테고리 | 생성 | tacit vs 구조 | freshness |
|---|---|---|---|---|
| Anthropic **CLAUDE.md** 가이드 (claude.com/blog/using-claude-md-files) | 7섹션(요약·디렉토리·컨벤션·테스트·명령·의존성·커스텀툴), "10분 설명거리"·반복 명령 강조 | `/init` 1패스 또는 수동 | 도메인 패턴 권장하나 대체로 구조 | **크기한도·갱신지침 없음** |
| **repowise** | 의존성·소유권·hotspot·co-change·bus factor·ADR | 연속 | 구조+위험신호 | ✅ freshness 스코어링 |
| DeepWiki / Cursor / Greptile / Mintlify | overview·임베딩·코드그래프·API문서 | 1패스~연속 | 대부분 구조 | 대체로 약함 |

요지: 상용은 **구조적 사실**(디렉토리·의존성·임베딩)에 강하고 **비자명 tacit 지식**(gotcha·근거·"바꾸기 전 알아야 할 것")은 Meta 5질문·CDT·Lore가 앞선다. **design-decision provenance·cross-branch 위험**을 잡는 도구는 아직 없음 — 우리가 겨냥한 지점.

### 7.4 합의 vs 갈림

- **합의(5/5)**: ①why는 코드 밖·시간에 얽힘 ②scope별 curated·distilled 산출물을 task-time 로드가 공통 형태 ③raw dump보다 distillation(토큰 예산) ④long-term 지식공학 ↔ task-time 컨텍스트공학 분리(CDT 명시).
- **갈림(우리에게 중요한 3축)**: ①**그래프 vs 평면** — 타입드 그래프는 CDT 하나뿐(우리 노선). ②**provenance** — 평면 context 파일엔 거의 없음, CDT=1급 엣지·Lore=commit-atomic 공짜. ③**freshness** — 평면 파일의 최대 약점(Heidelberg 실측이 증명), CDT·Lore·Meta가 각자 다른 답. **우리 2축 신선도(code-bound + 결정-계보)는 어느 선례에도 없는 고유 축**(§6-4와 일치).

### 7.5 palimpsest 접목

1. **§4.1 의미층 노드(`Summary/CommunityReport`)의 실증·스키마 후보** — Meta context 파일 = 그 노드의 배포 사례. 채울 스키마 후보 = **Meta 5질문 + Heidelberg 14 카테고리**.
2. **§4.2 엣지 분리의 온톨로지 보강** — HugRAG(2:1 매핑이라 "직접 설계" 필요)보다 **CDT 2층 모델이 더 직접적 선례**. `justified-by / constrained-by / has-responsibility / decomposes-to`를 inferred 엣지 어휘 출발점으로.
3. **§6-2(추론 엣지 precision) 외부 증거** — Meta는 path만 검증·의미 무검증, jprevanth의 의미-격차 비판, Meta 자인 "AI context가 유명 OSS 성공률↓" fail-case가 모두 **inferred 엣지가 오히려 해가 될 수 있음**을 뒷받침 → 가드레일 필요성 강화.
4. **§6-4(코드-결박 신선도) 외부 증거** — Heidelberg 실측(50% 무수정)이 "write-once는 썩는다"를 증명 → 우리 freshness 축이 차별점인 이유.

### 7.6 증거 등급 요약 (정직하게)

- Meta 40% = **n=6 preliminary 자기보고** — 가설이지 벤치마크 아님.
- CDT = **비전만, 평가 0**; 논문 2개 중 2510.16395는 **철회**, 2503.07967이 실체.
- Lore = **구현·평가 없는 제안**.
- Confucius = **유일 실평가**지만 그래프·staleness 없음, 숫자는 논문 자기보고(미재현).
- Heidelberg = **유일 실측**(채택률·14카테고리·50% 무수정) — 가장 신뢰할 경험 근거.
- 이 절 전체가 **§1~§6의 3표 적대검증을 안 거침** — 반영·결정 전 필요하면 핵심 주장만 재검증 권장.

---

## 부록 — 1차 출처

- Glean: engineering.fb.com/2024/12/19/developer-tools/glean-open-source-code-indexing/ · github.com/facebookincubator/Glean · glean.software/docs/angle/guide/
- CodeCompose: arxiv.org/abs/2305.12050
- HugRAG/CausalRAG2: arxiv.org/html/2602.05143v2 · /v1 · arxiv.org/abs/2602.05143
- CPG/Joern: cpg.joern.io · docs.joern.io/code-property-graph/
- Graphiti/Zep: github.com/getzep/graphiti · arxiv.org/abs/2501.13956
- neo4j-graphrag: neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html

### §7 확장 라운드(2026-07-01) 출처

- Meta tribal-knowledge(five-questions): engineering.fb.com/2026/04/06/developer-tools/how-meta-used-ai-to-map-tribal-knowledge-in-large-scale-data-pipelines/
- Code Digital Twin (CDT): arxiv.org/abs/2503.07967 · (철회본) arxiv.org/abs/2510.16395
- Lore (commit-trailer 지식 프로토콜): arxiv.org/abs/2603.15566
- Context Engineering for AI Agents in OSS (Heidelberg): arxiv.org/abs/2510.21413
- Confucius Code Agent (Meta+Harvard): arxiv.org/abs/2512.10398
- 2차·비평: techjacksolutions.com/ai-brief/the-context-problem-in-enterprise-agentic-ai-what-metas-trib/ · medium.com/@jprevanth (의미-격차 비평)
- 상용 가이드: claude.com/blog/using-claude-md-files · repowise.dev/blog/comparisons/best-codebase-documentation-tools-ai-agents
