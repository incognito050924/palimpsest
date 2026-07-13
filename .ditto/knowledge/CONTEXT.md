# palimpsest — DITTO Knowledge Context

Durable project knowledge lives under `.ditto/knowledge/`. This file is seeded
empty by `ditto init`; DITTO's knowledge-update flow appends durable decisions,
agreed terms, and learnings over time.

- glossary: `glossary.json`
- decisions: `adr/`

## 합의 용어 (glossary headline)

- **palimpsest** — 코드 표면 아래 의사결정·의도 층을 다시 읽게 하는 Knowledge Graph 기반 장기기억·지식 큐레이터.
- **Knowledge Graph (본체)** — palimpsest의 핵심 표현(엔티티+관계+온톨로지+provenance+신선도, 모든 엔티티 1급).
- **GraphRAG (회상층)** — KG 위 그래프탐색+벡터+LLM 합성 회상층, 출력은 근거 결박(출처+gap+confidence).
- **design-risk slice** — v1 첫 수직 슬라이스(브랜치 간 설계위험 감지).
- **Code Property Graph (CPG)** — 코드 정적분석을 담는 language-agnostic property graph(노드=타입+속성, 라벨 엣지, 다중 엣지). 정적층 온톨로지 출발점.
- **edge_kind** — KG 엣지의 출처·신뢰 분리축: deterministic(구조) vs inferred(외부 에이전트/LLM 추론·confidence 게이팅, 예: SUMMARIZES). 세탁 금지(결정론 writer verbatim 재사용 금지)를 writer+테스트로 강제(deterministic⊎inferred==total ∧ NULL==0). slice 4에서 inferred 첫 적재.
- **Episode (provenance 노드)** — git commit/SourceCommit을 가리키는 ground-truth 노드. 모든 inferred 엣지가 역참조되는 provenance 앵커.
- **Summary (node)** — 의미층 노드. 외부 에이전트가 생성한 tacit '왜·함정' 요약. id는 `summary:<sha256>` 네임스페이스라 코드 노드와 충돌 불가.
- **SUMMARIZES (edge)** — Summary→코드 노드 추론 엣지(edge_kind='inferred'). 회상 traversal 화이트리스트 제외 → 'summaries' 분리 채널로만 노출.
- **의미층 (semantic layer)** — v1 결정론 구조층 위에 얹는 생성형 지식층. slice 4가 첫 적재 계약을 실현(ADR-20260701-semantic-layer-load-contract).
- **branch-scoped identity (브랜치 스코프 노드 정체성)** — 노드 id(MERGE 키)에 접힌 branch 차원. None이면 bare qualified_name(byte-identical), named면 `branch:{branch}\x1f{qname}`. 같은 심볼의 브랜치 버전이 붕괴 없이 공존 → Reconcile 전제. ADR-20260703이 실현.

전체 정의는 `glossary.json` 참조.

## 결정 (ADR headline)

- **ADR-20260626-foundational-architecture** (active) — palimpsest 정초 아키텍처: 본체 = Knowledge Graph, 회상·합성 = GraphRAG(KG 의존), 이력 전부 보존, 캡처 자동 기본. → `adr/ADR-20260626-foundational-architecture.md`
- **ADR-20260701-v1-ontology-recall-reframe** (active) — v1 첫 슬라이스 재프레임: 설계위험 감지 → KG 온톨로지 구축 + GraphRAG 근거결박 회상. v1은 결정론 구조층만, 생성형·inferred는 유예. → `adr/ADR-20260701-v1-ontology-recall-reframe.md`
- **ADR-20260701-semantic-layer-load-contract** (active) — palimpsest 의미층 적재 계약: provider-free(LLM 호출 0), 외부 요약을 근거결박·edge_kind='inferred' 분리·provenance 강제로 적재, 회상은 'summaries' 분리 채널. → `adr/ADR-20260701-semantic-layer-load-contract.md`
- **ADR-20260703-branch-scoped-node-identity** (active) — 브랜치 스코프 노드 정체성: id에 branch 차원을 접어(scope_to_branch, None=bare byte-identical) 같은 심볼의 브랜치 버전이 붕괴 없이 공존. backfill의 '커밋별 버전드 안 만듦'을 branch 축에 한해 supersede, provider-free 유지. → `adr/ADR-20260703-branch-scoped-node-identity.md`
- **ADR-20260706-generative-curator-direction** (active) — 생성형 큐레이터 방향 회복: 격리 opt-in in-process 생산자 + git 선(先)물질화 → 기존 멱등 inferred 로더. provider-free를 전역→경로-스코프로 정련(recall+load 경로만 LLM-free), content-verdict는 외부 유지. 정밀 콜그래프는 주 build-less tree-sitter spine + 옵션 보조 CodeQL(도입 아님, 보안-Risk 니치 유예). → `adr/ADR-20260706-generative-curator-direction.md`
- **ADR-20260712-palimpsest-identity-host-neutral-generative-curator** (active) — palimpsest 정체성 불변식(최상위): ① 환경 비종속(소비자-일반 표면·소비자 개념 비내부화·역방향 종속 금지; ditto는 주·첫 소비자일 뿐) ② 생성형 큐레이터=핵심 목적. ADR-20260626/20260706을 supersede 않고 authority로 승격 + always-loaded 투영으로 드리프트 방지. → `adr/ADR-20260712-palimpsest-identity-host-neutral-generative-curator.md`
- **ADR-20260713-multilang-depends-on-name-local-boundary** (active) — 다언어 추출: ① DEPENDS_ON은 정적 타입주석 있는 언어에만(TS는 정적 타입 있으므로 약화 아닌 추가, `.js`엔 없음; query-compile 경계로 강제) ② SC-B — 이름 기반 CALLS/DEPENDS_ON은 각 언어 fragment 안에서만 해석, cross-language 연결은 IMPORTS specifier 해석만. → `adr/ADR-20260713-multilang-depends-on-name-local-boundary.md`

## 추출기 진척

- **다언어 ECMAScript 추출기 (#9, wi_260713lom, commit 4eaf85b)** — TS/JS/React/Svelte 정적 추출기 슬라이스 완료: 공유 `extract/ecmascript.py` 코어(`_EcmaWalker` + per-fragment 리졸버) + per-family 어댑터(typescript/javascript/svelte.py) + `extract_ecmascript` 통합 드라이버(ts→js→svelte fragment 격리 후 union-wide IMPORTS 해석). 범위 = IR 레벨(제품 ingest 배선은 #13에서). de-Class 하부(FUNCTION 노드 + community `_unit_of`의 Class∪Module 일반화)를 재사용. 결정 근거는 ADR-20260713.

## v1 초점

v1 초점 = **design-risk slice**: 구조·동작을 보장하고 위험판정 퀄리티는 다음 단계로 둔다. 전체 슬라이스 명세는 `.ditto/local/work-items/wi_2606263sn/intent.json` 참조(여기서 중복하지 않음).
