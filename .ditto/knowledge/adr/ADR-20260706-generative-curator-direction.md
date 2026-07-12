# ADR-20260706-generative-curator-direction — palimpsest 생성형 큐레이터 방향 회복: 격리 opt-in 생산자 + git 선(先)물질화, provider-free 정련

- 식별자: `ADR-20260706-generative-curator-direction` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-06
- work item: wi_260705lxy (reexam — 생성형 큐레이터 재검토, 설계+계획)
- 관계:
  - `ADR-20260701-semantic-layer-load-contract` §결정1(provider-free = LLM 호출 0, 생성 전적 외부)을 **정련/부분 supersede**한다. "어디서도 LLM 0"이라는 전역 해석을 **경로-스코프 해석**으로 좁힌다(아래 결정). ADR-20260701은 계속 active이며, 본 ADR이 그 불변식의 범위만 재정의한다.
  - `ADR-20260626-foundational-architecture` §2가 품은 "GraphRAG LLM 합성(근거결박)"(`:19,27`)과 **정합**하며, 그 정초 아키텍처가 이미 인가한 생성형 합성을 회복한다. VISION #4 Curate·#6(잠긴 결정)이 1급으로 못 박은 생성형 큐레이션(`VISION.md:36,51`)을 **되찾는다(RECLAIM)**.
  - `ADR-20260702-risk-designdecision-load-contract`(no-laundering, deterministic⊎inferred 세탁 금지)를 **supersede하지 않는다** — 그 형식층은 생성자 중립이라 그대로 유지·강화된다.
  - `ADR-20260704-semantic-embedding-load-contract`(인덱스당 단일 model·차원 pin)를 **supersede하지 않는다** — 임베딩은 외부 생산·single-model 유지, 본 회복 범위 밖(아래 유예 참조). `ADR-20260704:40(c)`가 명명한 "query-vector 생산 어댑터 추가 시 경계 재검토" 트리거의 정신을 curate 축에서 실현한다.

## 맥락

palimpsest는 정초 VISION에서 **생성형 큐레이터**(단순 아카이비스트가 아님)로 선언됐다(`VISION.md:20-22`). 그러나 slice 4 이후 누적된 `provider-free`(LLM 호출 0, 생성 전적 외부) 라인이 이 정체성을 조용히 뒤집었다 — as-built은 외부 추론을 적재하는 로더/아카이비스트에 가깝다. 이것이 정당한 방향인지, 되돌린다면 어디까지 되돌려야 하는지를, 실제로 얻은 것(결정론·hermetic 테스트·no-laundering·git-재구축)을 깨지 않으면서 재검토했다.

재검토는 연구(drift map) → 4역할 대칭 적대 검증(dialectic) → Synthesizer 판정(verdict=REVISE→Candidate B)의 근거 사슬을 거쳤다. 핵심 발견: **provider-free probe는 전역이 아니라 경로-스코프다** — `tests/recall/test_recall.py:117-145`는 `palimpsest.recall` import 폐포에 생성 모듈이 0인지만 검사하지, 배포물 어디에도 생성 코드가 없어야 한다고 요구하지 않는다. 즉 "격리된 생산자"는 probe를 깨지 않고 존재할 수 있다.

이 결정으로 보호해야 할 4속성:
- **E1 hermetic**: 테스트가 키·네트워크 없이 재현(`ADR-20260701:17,28`).
- **E2 no-laundering**: 판정을 구조 사실로 세탁 금지(deterministic⊎inferred==total ∧ NULL==0, `ADR-20260702-risk:29,77,81`).
- **E3 git-determinism**: 결정적 sha256 id + MERGE 멱등 → Neo4j drop→reload 동일 재현(`ADR-20260701:34`).
- **E4 external-judgment**: 생성자≠검증자 — content-verdict는 외부(`ADR-20260701:33`).

## 결정

**Candidate B** — palimpsest는 생성형 큐레이션을 1급 능력으로 회복하되, **격리된 opt-in in-process 생산자**를 통해 회복한다.

1. **격리 opt-in 생산자**: 신설 `palimpsest.curate` 모듈 + 별도 CLI 서브커맨드가 KB/코드를 읽어 요약 payload(grounding refs + gap + confidence)를 생성한다. 이 모듈은 `palimpsest.recall`/load surface를 import하지 않는다 — 경로-스코프 probe의 import 폐포 밖에 있으므로 probe는 green을 유지한다. 기본 파이프라인(backfill/reconcile)에는 포함하지 않고, 명시적 opt-in 서브커맨드로만 호출한다.

2. **git 선(先)물질화 → 기존 멱등 로더**: CLI가 생성 payload를 `summaries/<deterministic-id>.json` 등 git-tracked SoT에 **먼저** 물질화한다(비결정 LLM 출력은 이 커밋 시점에 동결). 그다음 기존 멱등 inferred 로더(`kg/summary.py:308` 등)가 그 파일만 읽어 `summary:` sha256 id + MERGE로 적재한다 — `edge_kind='inferred'`, deterministic ingest 경로 재사용 금지(E2 무변경).

3. **provider-free 불변식 정련(NARROWING)**: "palimpsest는 어디서도 LLM 0" → **"palimpsest의 recall+load 경로는 LLM-free(경로-스코프 probe로 강제·green 유지); 생성은 격리된 opt-in git-물질화 생산자; content-verdict는 외부 유지."** 생성 위치는 격리 in-process **또는** 외부 둘 다 허용하되, 어느 쪽이든 payload를 git-SoT로 물질화한 뒤 로더를 통과한다.

4. **content-verdict 외부 유지(무변경)**: curate는 요약(합성)만 생산하고, 판정(이 요약이 참인가)은 생산하지 않는다. 로더는 form(ref resolve)만 검사하고 판정 참/거짓은 검사하지 않는다(`ADR-20260701:33` 무변경). 이것이 E4를 보존하며 Candidate C(전면 역전)와의 결정적 분기다.

5. **provenance 자기귀속 정직성**: `generator`/`model` 필드가 palimpsest 자기 자신을 가리키지 않고 실제 생성 모델을 기록한다.

6. **정밀 콜그래프 축 — 주(主) in-house tree-sitter spine + 보조(옵션) CodeQL overlay**: 정밀도의 주경로는 palimpsest가 **소유하는 build-less tree-sitter 정밀 spine**이다. 현행 name-based over-matching(`src/palimpsest/extract/java.py` — `CALLS`가 호출을 그 단순명을 가진 **모든** 메서드에 연결)을 in-house로 개선한다 — tree-sitter `tags.scm` + `locals` 스코프 해소 + receiver-type 휴리스틱으로 좁힌다. 이 경로는 다언어·전이력 균일(임의 git 체크아웃, 빌드 불요)·palimpsest 소유이며, test-impact 필요(design-notes D §0 pain)를 푸는 **1급 경로**다.

   **CodeQL은 선택적 보조(auxiliary) HEAD-only overlay**이며 spine의 의존이 **아니다**. 빌드 가능한 HEAD에서 ① 정밀 부스트(virtual dispatch, dataflow) ② security/taint Risk 산출을 더한다 — build-less가 대체하지 못하는 **유일한 역할이 interprocedural taint(보안 Risk)**다. curate와 동일한 격리-생산자/git-물질화 패턴(findings를 git-SoT에 물질화 후 로더로 적재), coverage-asymmetry를 provenance로 정직 표기. 지금은 드롭 가능하며, 결정론적 security-Risk 생산자가 실제로 필요해질 때 채택한다. build-dependency는 HEAD-only로 격리하고 전이력 균일성은 tree-sitter spine이 담보한다.

   **연구 근거(2026 웹 조사)**: build-less + 다언어 + 정밀 콜그래프를 동시에 만족하는 성숙한 off-the-shelf 도구는 **없다**(트릴레마). 따라서 build-less 정밀은 마법 도구에서 조달하는 게 아니라 **소유(tree-sitter 개선)**해야 하고, CodeQL은 대체 불가한 니치(security-Risk)를 위해 옵션 보조로만 남긴다.
   - 정밀 도구는 전부 **빌드 필요**(전이력 균일성 위배): SCIP indexer(scip-java/python/typescript, https://sourcegraph.com/docs/code-search/code-navigation/precise_code_navigation), LSP 서버, Kythe, Glean.
   - build-less 다언어 도구는 **구문·단일 파일 한정**(콜그래프 없음): tree-sitter `tags.scm`/`locals`(로컬 스코프만, https://tree-sitter.github.io/tree-sitter/4-code-navigation.html), ast-grep(https://ast-grep.github.io/).
   - **stack-graphs**(build-less cross-file 후보)는 **2025-09-09 아카이브**(read-only; 마지막 릴리스 2024-12-13; Kotlin 미지원; pre-1.0 룰셋), https://github.com/github/stack-graphs.
   - **sound한 정적 콜그래프는 없다** — CodeQL조차 reflection/DI/dynamic-dispatch 엣지를 놓친다(ISSTA 2024 "Total Recall? How Good Are Static Call Graphs Really?", https://dl.acm.org/doi/10.1145/3650212.3652114). test-impact의 유일한 ground truth는 런타임 coverage(build+run, HEAD-only)다.

7. **임베딩 분리 명시**: 임베딩 축은 본 회복 범위 밖. 외부 single-model·차원 pin 유지(`ADR-20260704` 무저촉).

## 근거 (rationale)

- 3후보(A 현행유지 / B 격리 생산자 / C 전면 역전) 중 **B만이 E1–E4를 전부 보존하면서 VISION #6과 정합**하는 유일 행이다(Synthesizer 비교표). A는 VISION #6·정초 §2 열에서 위배(❌), C는 E1/E3/E4에서 파괴(❌).
- **4속성 전부 3중 장치로 보존된다**: git-materialize-first(E3) + path-scoped probe(E1) + external-verdict(E4). E2(no-laundering)는 생성자 중립이라 curate 유무와 무관하게 유지된다(양 Opponent 합의, dialectic 수렴 #1).
- 핵심 코드 사실: **probe는 경로-스코프다**(`tests/recall/test_recall.py:117-145` — `palimpsest.recall` import 폐포에 생성 모듈 0을 검사). 격리 생산자가 이 폐포 밖에 있는 한 probe는 green이다 — 이것이 "격리"의 기계적 정의다.
- 유지측(A)이 못 답한 반론("무기한 유예 = 실무 lock-OUT")을 B가 실제 회복 슬라이스 출하로 해소한다. 회복측(C)이 못 답한 반론("생성자≠검증자")을 B가 content-verdict 외부 유지(`ADR-20260701:33`)로 해소한다.
- inferred-layer 골격(edge_kind=inferred 파티션·grounding·atomic-reject·generator/model·분리 채널)이 이미 구축돼 있어, 생성기 연결은 load contract 재작성이 아니라 **additive 사용**이다.

## 기각한 대안 (rejected alternatives — 헌장 §4-10, 1급 기록)

- **Candidate A — provider-free 영구유지 (현행 동결)**: 기각. VISION #6(생성형 1급, "재론 아님")과 정초 `ADR-20260626 §2`(LLM 합성 포함)를 초과폐기(월권)한다. 생성 자유를 정당화한 전제(standalone)로 오히려 그 자유를 뺏는 **역방향 drift**를 남긴다. "deferred, not forbidden"은 텍스트상 참이나 회복 슬라이스가 0이라 실무적으로 무기한 lock-OUT과 구별되지 않는다. 강화형(A′: 외부 생산자 파이프라인 출하)조차 생성을 제품 경계 밖에 영원히 두어 "palimpsest가 생성형 큐레이터"라는 명제를 만족 못 하고, standalone 자기완결(`VISION.md:46,50`)을 외부 역의존으로 훼손한다.
- **Candidate C — 전면 역전 (생성+판정+임베딩 모두 in-process)**: 기각. content-verdict를 in-process로 끌어들이는 순간 **E4(생성자≠검증자)가 파괴**되어 자기-인증 고리가 닫힌다(`ADR-20260701:33` 위배). hermetic probe 5경로 붕괴(E1), 비결정 생성 in-process로 git-determinism 위협(E3), 그리고 SC1 scope-creep(회복은 요약 합성 1급이지 판정·임베딩 전부의 in-process화가 아니다). B가 content-verdict를 외부로 유지하는 것과 C의 결정적 차이가 이 지점이다.
- **build-less 정밀 콜그래프를 off-the-shelf 도구로 조달 (§결정6 축)**: 기각 — 그런 도구가 없다(트릴레마, 위 연구 근거). 검토·기각한 후보: **stack-graphs**(build-less cross-file였으나 2025-09-09 아카이브·read-only), **SCIP indexer / LSP / Kythe / Glean**(정밀하나 빌드 필요 → 전이력 균일성 위배). 결론: build-less 정밀은 소유(tree-sitter spine 개선)하고, CodeQL은 대체 불가 니치(security-Risk)를 위한 옵션 보조로만 둔다.

## 거버넌스 (ADR-0020 / 헌장 §4-10)

`ADR-20260702-risk-designdecision-load-contract:92`가 provider-free를 **hard invariant**로 선언하고 완화를 별도 결정으로 유예했다. 이 완화는 **intent-level 충돌**이므로(work item의 목적 자체가 hard invariant가 금지한 것을 요구) 에이전트가 단독으로 풀 수 없고 **사용자만 해소**할 수 있다.

사용자가 deep-interview(wi_260705lxy)에서 전 스펙트럼(유지 / 완화 / 전면 역전)을 열어 검토하고, 완화 방향을 확인한 뒤 착수를 승인했다(dialectic 수렴 #5). 따라서:
- **사용자가 intent를 해소**했다(hard invariant 완화 승인).
- **에이전트가 방법을 결정**했다(Candidate B — 격리 생산자 + git 선물질화 + verdict 외부).

이는 에이전트의 무단 hard-invariant 완화가 **아니다**. 사용자 소유의 의도 결정 위에서 에이전트가 방법을 선택한 것이다.

## 유예·범위 밖 (명시)

- **구현**: 본 work item은 설계+계획만이다(소스 변경 0). `palimpsest.curate` 모듈·CLI·로더 배선의 실제 구현은 백로그의 후속 work item이 담당한다.
- **VG1 — ADR 전수성**: 10개 중 5개 ADR만 정독했다(backfill · community-deterministic · communityreport · decision-lineage · branch-scoped 미정독). "생성을 영구 금지한 ADR 부재"는 전수 grep이 아닌 판단이며, verify 노드/후속이 전수 확인해야 한다.
- **VG2 — rebuild-determinism 실행 검증 (확인됨 2026-07-13)**: "생성 출력을 git-persist + MERGE 멱등하면 drop→reload가 실제로 동일 재현한다"는 주장이 **실행으로 확인됐다**. 근거(testcontainers Neo4j 5, green): `tests/kg/test_curate_integration.py::test_curate_rebuild_is_deterministic`(drop→같은 git-SoT 재적재→노드·엣지·id·edge_kind·grounding 동일) + `tests/e2e/test_cli_e2e.py`의 drop-and-reload 2건(semantic_verdict·embedding/vector-index) + summary/risk `test_reload_is_idempotent`. 초기 설계 주장의 미검증 상태 해소. (재개 트리거는 아래 change_condition (VG2) 유지.)
- **임베딩 in-process화**: 범위 밖. 외부 single-model 유지.
- **판정(content-verdict) in-process화**: 명시 금지. 외부 유지.
- **CodeQL 세부 edge_kind 표기 규칙**: CodeQL 슬라이스에서 확정(design-notes 개방 질문).

## 철회·변경 조건 (change_condition)

관찰 가능한 트리거로, 사용자가 재검토를 원하는 조건을 결박한다. 아래 중 하나가 관찰되면 본 결정을 재검토한다.

- **(a) 외부 도구 비용/신뢰성 저하**: 외부 생산자·판정 도구의 비용 또는 신뢰성이 in-house 경로 아래로 떨어질 때.
- **(b) 확장 요구를 외부 생산자가 감당 못 함**: 규모·다언어 확장을 외부 생산자가 서비스하지 못할 때.
- **(c) 외부-only 회상/판정 품질이 목표 미달**: 외부-only 구성에서 회상 또는 판정 품질이 목표치 아래일 때.
- **(d) 자기완결성/외부의존이 방향을 훼손**: 외부 의존이 palimpsest의 standalone 자기완결 방향을 훼손할 때.
- **(VG2) git-persist rebuild-determinism 실패**: VG2 검증에서 생성 출력의 git-persist가 drop→reload 동일 재현을 보장하지 못하는 것으로 판명되면 재개(E3 위협).
- **(격리 붕괴)**: 격리가 실제로 유지되지 않아 경로-스코프 probe가 `palimpsest.curate`를 잡기 시작하면 재검토(E1 위협).
