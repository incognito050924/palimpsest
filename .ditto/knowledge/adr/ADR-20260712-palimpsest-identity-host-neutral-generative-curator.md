# ADR-20260712-palimpsest-identity-host-neutral-generative-curator — palimpsest 정체성 불변식: 환경 비종속 + 생성형 큐레이터

- 식별자: `ADR-20260712-palimpsest-identity-host-neutral-generative-curator` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-12
- work item: wi_26071277e

## 맥락

palimpsest의 두 정체성 축은 정초에서 이미 선언됐다 — **생성형 큐레이터**(VISION 잠긴결정 #6, `ADR-20260626 §2` LLM 합성)와 **standalone/환경 비종속**(VISION 잠긴결정 #1·#4·#5, `DESIGN.md:18,74` "ditto는 첫 소비자일 뿐, 노출을 ditto에 특화하지 않는다"). 그러나 이 두 사상의 **가장 강한 진술이 전부 비권위 문서에 있었다**: VISION은 스스로 "배경 지도, 권위 아님"(`VISION.md:5`), DESIGN은 스스로 "계획 문서 — ADR·코드로 흡수되면 폐기"(`DESIGN.md:6`)라고 선언한다. 권위 층(ADR/glossary/CONTEXT)에는 파편적으로만 존재했다.

결과는 관측된 드리프트다:

- **생성형 축**: slice 4 이후 누적된 `provider-free` 라인이 "생성형 큐레이터"를 조용히 "외부 추론을 적재하는 아카이비스트"로 뒤집었다(→ `ADR-20260706` 재검토·회복). 선언은 VISION에 있었으나 실무 슬라이스가 반대로 갔다.
- **환경 비종속 축**: 정체성-핵심 `ADR-20260706`이 always-loaded 투영(`CONTEXT.md` 결정 headline, `CLAUDE.md` DITTO Knowledge 요약)에 **미투영**이라, 매 세션 로드되는 표면에 "생성형 큐레이터 + 환경 비종속"이 뜨지 않았다. 그 공백 때문에 소비자(ditto ACG) pre-compute 필요(ditto issue #9)를 논할 때 palimpsest를 ditto에 결합시키는 쪽으로 추론이 드리프트했다.

즉 **"VISION에 선언" ≠ "드리프트 방지"**임이 실증됐다. 헌장 §4-11(권위는 코드·살아있는 지침에 있다)에 따라, 두 축을 권위 층에 1급 불변식으로 승격하고 always-loaded 투영으로 상시 노출해야 한다. 사용자가 이 승격을 명시 결정했다(2026-07-12: "최상위 목표로 승격").

## 결정

palimpsest의 두 정체성 불변식을 authority에 못박는다. 이 ADR은 `ADR-20260626`(정초)·`ADR-20260706`(생성형 회복)을 **supersede하지 않고**, 그 위에 정체성 불변식으로 명문화·승격한다.

### 결정 1 — 환경 비종속 (host-neutral)

palimpsest는 어떤 소비 환경에도 종속되지 않는다. ditto는 **주(main) 소비자이자 첫 소비자일 뿐**이며, 특정 호스트가 아니다. 세부 3항:

- **(a) 소비자-일반 표면**: palimpsest는 자기 일반 어휘(`Class·Method·CALLS·DEPENDS_ON·test-impact` 등)를 소비자-중립 표면(MCP/CLI `load`·`query`)으로 노출한다. 특정 소비자를 위한 특화 표면을 만들지 않는다(`DESIGN.md:74` "ditto는 첫 소비자일 뿐, 노출을 ditto에 특화하지 않는다").
- **(b) 소비자 개념 비내부화**: 소비자의 도메인 개념(예: ditto ACG의 `ImpactGraph·ChangeContract·ArchitectureSpec`)을 palimpsest 온톨로지·코어에 **내부화하지 않는다**. 소비자 어휘로의 매핑은 소비자 binding 쪽 책임이다(design-notes C-Q4). VISION 잠긴결정 #4 "seam 대체(기능 이식 아님)"의 실현.
- **(c) 역방향 종속 금지**: palimpsest 코어 기능이 특정 호스트의 산출물에 의존하지 않는다. 외부 producer(임의의 정적분석·정규화 색인 도구 등)의 팩트는 **producer-중립 `load` 계약**으로 수용하며, palimpsest는 그 출처가 ditto인지 알 필요가 없다. "ditto가 생산한 X를 소비"라는 특정 명명은 "임의 외부 producer via load"로 읽는다(design-notes §211이 이미 producer-중립). *(정밀 구조 추출의 1급 경로는 palimpsest가 소유하는 build-less tree-sitter spine이며, CodeQL 등 빌드 의존 엔진은 도입이 아니라 옵션 보조로 유예됨 — `ADR-20260706 §결정6`.)*

### 결정 2 — 생성형 큐레이터 (핵심 목적)

palimpsest는 단순 아카이브/사서가 아니라 **조합형 + 생성형 큐레이션으로 새 가치를 합성**하는 것이 핵심 목적이다(`VISION.md:22` "큐레이터다(단순 아카이브/사서가 아니다)", VISION 핵심기능 #4 Curate, 잠긴결정 #6). 이는 `ADR-20260626 §2`(GraphRAG LLM 합성)와 `ADR-20260706`(격리 opt-in 생산자로 생성형 회복)의 정체성 결론을 재확인·승격한다. 생성형 출력은 **출처 + gap(모르는 것) + confidence 계급으로 사실과 분리**한다(세탁 금지 — E2 no-laundering, `ADR-20260702-risk` 무저촉).

### 집행 (enforcement)

- **always-loaded 투영**: 이 ADR과 `ADR-20260706`을 `CONTEXT.md` 결정 headline에 반영하고, `ditto bridge knowledge`로 `CLAUDE.md` DITTO Knowledge 블록을 재투영해 두 축이 매 세션 로드되는 표면에 상시 노출되게 한다. VISION `docs/VISION.md`의 최상위(목적/목표)에도 두 축을 명시 목표로 편입한다.
- **코드 probe는 현재 미도입**: 코어에 호스트 결합이 아직 없어 예방적 probe는 과하다(YAGNI). 결정 1을 위협하는 결합(예: 코어가 소비자 심볼을 import)이 실제로 생기면, provider-free path-scoped probe(`tests/recall/test_recall.py:117-145`) 선례대로 격리 probe를 신설한다 — 그때 이 ADR의 change_condition이 아니라 집행 강화다.

## 근거 (rationale)

- **드리프트가 이미 두 번 이 경로로 발생**: 선언을 비권위 문서에만 두면 실무 슬라이스가 반대로 가도 잡히지 않는다(provider-free 드리프트, 그리고 이번 대화의 ditto-결합 추론 드리프트). 권위 승격 + always-loaded 투영이 유일하게 실효적인 방지책이다(헌장 §4-11).
- **기각 — VISION/DESIGN 강화만**: VISION·DESIGN은 스스로 "권위 아님/폐기 조건 있음"을 선언한 층이라, 거기서 아무리 강하게 써도 authority 드리프트를 못 막는다. 이미 그래서 뒤집혔다.
- **supersede 아님**: `ADR-20260626`·`ADR-20260706`의 결정을 되돌리지 않는다. 두 축은 이미 그 ADR들에 함의돼 있고, 이 ADR은 그것을 **1급 불변식으로 명문화 + 투영 집행**을 추가할 뿐이다.
- **환경 비종속과 생성형의 상호 근거**: `ADR-20260706`이 규명하듯, 생성 자유를 정당화한 전제가 바로 standalone(외부 advisory 자세에 구속 안 됨)이다. 두 축은 서로의 근거다 — standalone이라 생성형 1급이 가능하고, 생성형 큐레이터라는 목적이 standalone을 요구한다.

## 관계

- `ADR-20260626-foundational-architecture`(정초)·`ADR-20260706-generative-curator-direction`(생성형 회복)을 supersede하지 않고 정체성 불변식으로 승격·명문화한다.
- VISION 잠긴결정 #1(standalone)·#4(seam 대체)·#5(ditto=소비자)·#6(생성형 1급)과 `DESIGN.md:18,74`를 authority로 끌어올린다.
- `ADR-20260702-risk-designdecision-load-contract`(no-laundering)를 무저촉으로 유지·강화(생성형은 출처+gap+confidence 분리).
- ditto `ADR-0021`(memory seam = 외부 독립 프로젝트, ditto=consumer)과 정합 — 환경 비종속은 ditto 측 결정과도 같은 방향.

## 철회·변경 조건 (change_condition)

- **비전 차원 재정의에서만 재론**: 사용자가 palimpsest를 (a) 특정 호스트 전용 제품으로 재정의하거나, (b) 큐레이터 정체성을 단순 아카이브로 재정의할 때만 이 불변식을 재론한다. 구현 선택·소비자 구성의 변화로는 흔들지 않는다.
- **결정 1(c) 위협 관측 시 집행 강화**(철회 아님): palimpsest 코어에 특정 호스트 결합이 실제로 생기면 격리 probe를 신설해 결정 1을 기계적으로 강제한다.
