# ADR-20260701-semantic-layer-load-contract — palimpsest 의미층 적재 계약: provider-free, 근거결박·inferred 분리·분리 채널 회상

- 식별자: `ADR-20260701-semantic-layer-load-contract` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-01
- work item: wi_260701cjf (slice 4 — 의미층 첫 적재 계약)
- 관계: `ADR-20260626-foundational-architecture`가 품은 "GraphRAG LLM 합성(근거결박)"의 첫 실현. `ADR-20260701-v1-ontology-recall-reframe` §결정3이 유예(deferred, not forbidden)한 생성형·semantic 요약·inferred 엣지 층의 **적재 계약을 실현**한다 — 그 ADR을 supersede하지 않는다(계속 active, 본 ADR이 후속을 구체화). 같은 ADR의 edge_kind 노트(line 25)가 "향후 inferred writer는 deterministic/inferred 분리를 규약으로 지켜야 한다"며 이 층을 선인가했다.
  - `ADR-20260706-generative-curator-direction` §결정3이 본 ADR §결정1의 provider-free 불변식을 **전역 해석에서 경로-스코프 해석으로 정련(narrowing·부분 supersede)**한다. 본 ADR은 계속 active이며, §결정1 본문에 정련 노트를 남긴다(아래). 임베딩(`ADR-20260704-semantic-embedding-load-contract`)·no-laundering(`ADR-20260702-risk-designdecision-load-contract`) 무저촉.

## 맥락

v1(ADR-20260701-v1-ontology-recall-reframe)은 결정론 구조층(코드→KG)만 실현하고 생성형·semantic 요약·inferred 엣지는 유예했다. 다만 유예는 금지가 아니었고, edge_kind 분리 규약이 이 층을 선인가했다. slice 4는 그 유예된 **의미층(semantic layer)의 첫 적재 계약**을 실현한다 — 코드에서 결정론적으로 추출되지 않는 tacit 지식(왜·함정·설계 의도)을 그래프에 얹되, 그것이 구조 사실로 세탁되지 않도록 형식(근거결박)을 강제한다.

## 결정

palimpsest 의미층 적재 계약을 다음으로 결정한다.

1. **palimpsest의 recall+load 경로는 LLM-free (경로-스코프 probe로 강제, green 유지).** 요약 **생성**은 격리된 opt-in git-물질화 생산자(신설 `palimpsest.curate` — recall/load import 폐포 밖) **또는** 외부 에이전트가 담당하며, 어느 쪽이든 payload를 git-SoT로 먼저 물질화한 뒤 기존 멱등 로더를 통과한다. **content-verdict(판정 참/거짓)는 외부 유지**(생성자≠검증자). recall/load 경로에 provider를 박지 않으므로 테스트가 hermetic(키·네트워크 없이 재현)하다.

   > **정련(2026-07-06, `ADR-20260706-generative-curator-direction` §결정3; 구현 착지 wi_260712t3e).** 원래 이 결정은 "palimpsest는 provider-free(어디서도 LLM 0), 생성은 전적 외부"였다. ADR-20260706이 이를 **전역 해석에서 경로-스코프 해석으로 좁혔다(narrowing·부분 supersede)**: 금지되는 것은 recall+load 경로의 LLM(경로-스코프 probe가 강제)이지 배포물 전체가 아니다. 격리 opt-in 생산자(`palimpsest.curate` + 별도 CLI 서브커맨드)는 그 import 폐포 밖에 있어 probe를 깨지 않는다 — 코드로 확인: `import palimpsest.curate`의 폐포에 `palimpsest.recall`/load가 0(역방향 격리 테스트), 그리고 curate 설치 하에서도 5개 경로-스코프 probe green + 위반 주입 시 red(음성 케이스). **무저촉 명시**: 임베딩 single-model·차원 pin(`ADR-20260704-semantic-embedding-load-contract`)과 no-laundering(deterministic⊎inferred==total ∧ NULL==0, `ADR-20260702-risk-designdecision-load-contract`)은 생성자 중립이라 이 정련의 영향을 받지 않는다.
2. **적재 시 3중 형식 강제.**
   - **근거결박(grounding)**: 요약의 모든 claim이 ≥1 source ref를 갖고, 그 ref가 실재 그래프 노드/코드로 resolve돼야 한다. 미해소 시 **summary-atomic 거부** — 실패한 요약만 거부하고 나머지는 적재한다.
   - **edge_kind='inferred' 분리**: 결정론 writer를 verbatim 재사용하지 않는다. Summary→코드 엣지(SUMMARIZES)는 `edge_kind='inferred'`로 마킹(deterministic⊎inferred==total ∧ NULL==0을 writer+테스트로 강제).
   - **provenance**: `code_bound_at`=해소된 대상 코드의 committed_at, `created_at`=요약 생성 시각, `generator`/`model` non-null.
3. **회상에서 'summaries' 분리 채널로 노출.** 병합 prose 없음. SUMMARIZES는 traversal 화이트리스트에서 제외돼 items로 누출되지 않는다.
4. **회상 경로 LLM-free 불변식 유지.** fresh-interpreter probe로 회상 경로에 생성형 라이브러리 0을 강제한다.

## 근거 (rationale)

- v1의 "도메인 지식 없는 구조 검증"을 생성형에 잇기 위해 **형식(근거결박) 검증**을 채택한다 — 이번엔 요약의 모든 주장이 실제 코드 출처로 resolve되는지(형식)만 검증하고, 요약이 근거를 의미적으로 뒷받침하는지(내용)는 유예한다.
- provider를 코드에 박지 않아 테스트가 hermetic·키·네트워크 없이 재현 가능하다.
- ADR-20260626이 품은 "LLM 합성(근거결박)"의 첫 실현이며, ADR-20260701-v1-ontology-recall-reframe이 유예한 층을 규약대로(deterministic/inferred 분리) 잇는다.

## 유예·범위 밖 (명시)

- **내용(semantic) 검증**: 요약이 근거를 의미적으로 뒷받침하는지 판정 — ~~미도입(이번은 형식 검증만).~~ → **부분 실현(2026-07-02, wi_260701ulo)**: 판정 자체는 여전히 palimpsest 밖(외부 판정자=ditto, provider-free 유지)이고, palimpsest는 외부 verdict를 `Summary.semantic_verdict`(생성기 confidence와 분리된 dedicated 필드)로 **ingest·annotate**만 한다 — unfaithful도 거부 않고 로드, 회상이 flag로 노출. **판정 하네스·라벨 코퍼스·per-claim은 후속**(코퍼스는 Java 전용 추출기 제약 있음).
- **요약 durability**: ~~현재 Neo4j-only — DB drop→rebuild 시 소실~~ → **실현(2026-07-02, wi_260701ffv)**: 외부 생성 요약 payload를 git-tracked `summaries/` 디렉토리에 SoT로 두고 CLI `load <dir>`로 재구축. 결정적 `summary:<sha256>` id + MERGE 멱등이라 Neo4j drop→reload가 동일 Summary·SUMMARIZES를 복원. git=SoT 방향은 ADR-20260626 #2(KG는 재구축 가능한 projection)를 요약에 적용한 것이며 provider-free 유지.
- 커뮤니티/설계결정/위험 대상, 자동 재생성 — 범위 밖.

## 철회·변경 조건 (change_condition)

- **내용 검증층 도입** 시(형식 검증을 넘어 요약이 근거를 의미적으로 뒷받침하는지 판정) 재검토한다. → **부분 충족**(2026-07-02, wi_260701ulo): verdict-ingest 배선(annotate) 실현, 판정 하네스는 후속. provider-free 불변식은 유지(판정은 외부).
- ~~**요약 durability 결정** 시(git-SoT 영속으로 Neo4j drop→rebuild 내구성 확보) 재검토한다.~~ → **충족·실현**(2026-07-02, wi_260701ffv): git-tracked `summaries/` SoT + `load <dir>` 재구축. 위 "유예·범위 밖" 참조.
