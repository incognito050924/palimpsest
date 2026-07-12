# palimpsest 생성형 큐레이터 재검토 — 실행 백로그

> **등록됨 (2026-07-12)**: 이 백로그는 GitHub 이슈 + work item으로 병합·우선순위 등록됐다.
> **P0** = T1·T2·T3·T4·T6·T7·T8 병합 → 이슈 `#3` + work item `wi_260712t3e`(9 AC, draft·미착수) · **P1** = T9-PRIMARY → 이슈 `#2` · **P2(옵션·유예)** = T10 → 이슈 `#4`. 설계-블록(A/B/D/E) = 이슈 `#5/#6/#7/#8`, 코드품질 substrate(F) = `#1`. 우선순위는 보드 Priority 필드(P0/P1/P2)가 SoT, 현행 상태는 이슈가 권위.

- work item: `wi_260705lxy` (reexam) — **이 WI는 설계+계획에서 멈춘다. 소스 변경 0.**
- 이 백로그는 **후속 구현 WI**가 집어갈 작업 목록이다. 여기의 어떤 항목도 이 WI에서 착수하지 않는다.
- 출처(권위): 확정 설계 `docs/design/palimpsest-generative-curator-reexamination.md`(§5 모듈 경계 · §6 충돌 해소 · §9 검증 공백)와 결정 `.ditto/knowledge/adr/ADR-20260706-generative-curator-direction.md`(§결정 1–7 · change_condition).
- 코드 앵커(현행): 격리 probe `tests/recall/test_recall.py:117-145` · `tests/recall/test_recall_semantic.py:207-233` · `tests/kg/test_summary.py:384-409`; no-laundering 불변식 `tests/kg/test_risk.py:160-199`; 멱등 로더 `src/palimpsest/kg/summary.py`(`load_summaries`); 기존 CLI `load` 서브커맨드 `src/palimpsest/cli.py`(외부 생산 payload → 로더 배선이 **이미 존재**).

## 이 백로그를 읽는 법

- 각 작업(T*)은 **수직 슬라이스**다: 하나의 change-target, 하나의 관찰 가능한 검증, 명시된 선행 의존.
- **change-target** = 이 작업이 건드리는 모듈/파일/계약을 설계가 허용하는 만큼 정확히.
- **verification** = 이 작업을 닫는 관찰 가능한 검사(대개 특정 테스트).
- **depends-on** = 먼저 착지해야 하는 작업.
- 순서: **First slice(T1–T4)**가 Candidate B의 최소 종단 경로(격리 생산자 → git 물질화 → 기존 로더 → 격리 증명 → 재구축 결정론)를 세운다. **Later(T5–T10)**는 전수성·정직성·정밀 spine(주)·CodeQL 보조(옵션)·거버넌스 마무리다.
- 각 작업은 헌장 §4-5대로 **red-first 또는 관찰 증거**로 닫는다. 코드 동작 작업(T1–T4, T6)은 실패 테스트 먼저. 조사/문서 작업(T5, T7 일부, T8)은 검사/리뷰 증거로 닫는다.

---

## First slice — Candidate B 최소 종단 경로

### T1 · 격리 opt-in `palimpsest.curate` 생산자 모듈

- **change-target**: 신설 `src/palimpsest/curate/`(모듈). KB/코드를 읽어 요약 payload(grounding refs + gap + confidence)를 생성. LLM 호출이 허용되는 **유일 구역**. **불변식: `palimpsest.recall`·load surface를 import하지 않는다.** LLM 클라이언트 의존은 신설 optional-dependency 그룹(T-dep, 아래)으로 격리. content-verdict(판정 참/거짓)는 **생성하지 않는다**(요약 합성만).
- **동반 change-target(T-dep, T1에 포함)**: `pyproject.toml` `[project.optional-dependencies]`에 `curate = [...]` 그룹 신설(LLM 클라이언트 등). 기본 설치·`test` 그룹은 무저촉 → 기본 파이프라인은 여전히 provider-free.
- **verification**:
  1. curate가 결정적 stub/fixture 입력에서 grounding+gap+confidence를 갖춘 payload를 산출하는 단위 테스트(신설 `tests/test_curate.py`). 실제 LLM 호출은 mock — hermetic 유지.
  2. **import-격리 단위 테스트**: `import palimpsest.curate` 후 그 모듈의 import 폐포에 `palimpsest.recall`/load가 없음을 단언(역방향 경계). 이는 T3의 recall-side probe와 쌍을 이룬다.
- **depends-on**: 없음(설계상 진입점).
- **red-first**: 예 — payload 생성은 코드 동작. 실패 테스트(payload 필드 부재 assert) → 최소 구현 → green.
- 근거: ADR §결정1; 설계 §5.2 `curate/` 블록.

### T2 · 물질화 CLI 서브커맨드 (git 선물질화)

- **change-target**: `src/palimpsest/cli.py`에 `curate` 서브커맨드 신설(기존 argparse 패턴 재사용). curate 출력을 `summaries/<deterministic-id>.json`으로 **git-tracked SoT에 write**. 비결정 LLM 출력은 이 write(→커밋)에서 동결. **적재는 하지 않는다** — 물질화까지만. 적재는 기존 `load` 서브커맨드(현행)가 담당.
- **verification**: CLI `curate` 실행이 결정적 payload 파일을 지정 경로에 쓰고, 그 파일이 기존 `load` 서브커맨드가 받는 스키마와 정합함을 단언하는 통합 테스트(mock된 curate 출력 사용). 파일이 recall/load 경로 밖에서 생성됨을 확인.
- **depends-on**: T1.
- **red-first**: 예 — CLI가 파일을 쓰는 동작. 파일 부재 assert → 구현 → green.
- 근거: ADR §결정2; 설계 §5.2 `cli(curate)` 블록, §5.3 3단계.
- **주의(기존 자산)**: 물질화된 payload를 로더로 흘리는 배선은 **신규 계약이 아니다** — `cli.py`의 기존 `load` 서브커맨드가 이미 "외부 생산 payload 디렉터리 → `load_summaries` 멱등 적재"를 한다. T2는 그 앞단(생성→물질화)만 채운다.

### T3 · 물질화 payload를 기존 멱등 inferred 로더로 배선 (신규 load contract 금지)

- **change-target**: **원칙적으로 무변경** — `src/palimpsest/kg/summary.py`의 `load_summaries`(sha256 `summary:` id + MERGE 멱등, `edge_kind='inferred'')와 CLI `load` 서브커맨드를 **그대로 재사용**. curate가 만든 payload가 이 로더를 추가 수정 없이 통과함을 증명하는 것이 작업의 실체(additive 사용, load contract 재작성 아님). 필요 시 payload 스키마 정합을 위한 **최소** 조정만, 그 조정도 deterministic ingest 경로 재사용 금지(E2) 유지.
- **verification**:
  1. curate-물질화 payload를 `load`로 적재 후 그래프에 `edge_kind='inferred'` 노드/엣지가 생기고 grounding 엣지가 결박됨을 확인하는 종단 테스트.
  2. **no-laundering 회귀**: `tests/kg/test_risk.py:160-199` 계열(det ⊎ inferred == total ∧ NULL == 0)이 curate payload 적재 후에도 green.
- **depends-on**: T2.
- **red-first**: 예 — 적재 결과는 코드 동작. curate payload가 로더를 통과하는 종단 assert.
- 근거: ADR §결정2(멱등 로더 재사용); 설계 §5.3 4단계, §1.4 R4.

### T4 · 격리 probe 확장 — curate 존재 하에서 recall+load가 LLM-free임을 증명 (E1 guard)

- **change-target**: `tests/recall/test_recall.py`(그리고 병렬 probe `tests/recall/test_recall_semantic.py`·`tests/kg/test_summary.py` 계열). 기존 경로-스코프 probe를 **`palimpsest.curate`가 설치·존재하는 상태에서** 실행해도 `palimpsest.recall`/load import 폐포에 생성 모듈이 0임을 계속 단언하도록 확장/보강. "격리"의 기계적 정의를 회귀로 고정.
- **verification**: curate 모듈(+ 그 optional-dep)이 존재하는 환경에서 probe 5경로가 여전히 green. 반대로 curate가 recall/load 폐포로 새어 들어가면(예: recall이 curate를 import) probe가 **red**가 되도록 음성 케이스 포함 — probe가 실제로 격리를 잡는지 확인(phantom green 방지).
- **depends-on**: T1(격리 대상 존재). T3와 병행 가능하나 T3 이후 실행이 자연스럽다.
- **red-first**: 예(음성 케이스가 red를 제공) — 격리 위반을 심어 probe가 red임을 확인 후 제거해 green.
- 근거: ADR §결정1·§근거(경로-스코프 probe = 격리의 기계적 정의); 설계 §5.2, §6.1 E1 행. change_condition (격리 붕괴) 트리거의 상시 감시자.

---

## Later — 전수성 · 정직성 · CodeQL 트랙 · 거버넌스

### T5 · VG1 — 미정독 5개 ADR 전수 스윕 (provider-free/생성 문장)

- **change-target**: 코드/소스 변경 없음(조사 작업). 대상 = `.ditto/knowledge/adr/`의 5개 미정독 ADR: backfill · community-deterministic · communityreport · decision-lineage · branch-scoped. 산출물 = 스윕 결과 기록(발견 시 좁혀야 할 문장 목록; 없으면 "미접촉 확인"). 좁힘이 필요한 문장이 나오면 그건 T8(supersede) 또는 각 ADR의 후속 정련 항목으로 넘긴다.
- **verification**: 5개 ADR 각각에 대해 provider-free/생성/LLM-금지 관련 문장의 유무를 grep+정독으로 전수 확인한 근거(파일:라인)를 남김. "영구 금지 ADR 부재"를 판단에서 **전수 확인**으로 승격. — 관찰 증거(inspection), red-first 비대상.
- **depends-on**: 없음(독립 조사, First slice와 병렬 가능).
- 근거: ADR §유예 VG1; 설계 §1.7, §9 VG1.

### T6 · VG2 — drop→reload 재구축 결정론 테스트

- **change-target**: 신설 테스트(예 `tests/test_rebuild_determinism.py` 또는 기존 로더 테스트 확장). curate-물질화 payload를 git-SoT에서 적재 → Neo4j **drop** → 동일 git-SoT에서 **reload** → 두 그래프가 동일(노드/엣지/id 집합)함을 단언. 생성 출력의 git-persist가 rebuild-determinism을 실제로 보장하는지 실행으로 확정.
- **verification**: drop→reload 후 그래프 스냅샷(id·edge_kind·grounding) 동등 비교가 green. hermetic(testcontainers Neo4j)에서 재현.
- **depends-on**: T3(물질화 payload가 로더를 통과해야 재구축 대상이 존재).
- **red-first**: 예 — 재구축 동등성은 코드 동작. 비결정성이 새면 red.
- 근거: ADR §유예 VG2 + change_condition (VG2); 설계 §5.3 5단계, §6.1 E3, §9 VG2. 이 테스트가 change_condition (VG2) 위협의 상시 감시자.

### T7 · provenance 자기귀속 정직성 규약

- **change-target**: curate가 만드는 payload의 `generator`/`model` 필드 기록 규약(주로 T1 `src/palimpsest/curate/`의 payload 생성부 + 로더가 이 필드를 보존/검증하는지 `src/palimpsest/kg/summary.py` 확인). 규약: `generator`/`model`이 **palimpsest 자기 자신을 가리키지 않고 실제 생성 모델을 기록**한다.
- **verification**: curate payload의 `generator`/`model`이 palimpsest 식별자가 아니라 실제 모델을 담음을 단언하는 테스트. 로더가 이 값을 세탁 없이 보존함을 확인.
- **depends-on**: T1(payload 생성부 존재).
- **red-first**: 예 — 필드 값 규약은 코드 동작. 자기귀속 값이면 red.
- 근거: ADR §결정5; 설계 §3.2(4), §5.5 provenance 자기귀속.

### T8 · ADR-20260701 §결정1 정식 supersede 편집 (구현 착지 후)

- **change-target**: `.ditto/knowledge/adr/ADR-20260701-semantic-layer-load-contract.md` §결정1 편집(문서). "어디서도 LLM 0" → **"recall+load 경로 LLM-free(경로-스코프 probe 강제) + 격리 opt-in git-물질화 생산자 허용; content-verdict 외부 유지"**로 정련(부분 supersede). ADR-20260706의 정련을 원본 계약에 반영. `ADR-20260704`(임베딩 single-model)는 무저촉임을 명시. `ADR-20260702`(no-laundering) 무변경 명시.
- **verification**: 편집된 §결정1이 ADR-20260706 §결정3 문구와 정합하고, 임베딩·no-laundering 무저촉을 명시하며, 구현(T1–T4)이 실제로 착지한 상태를 참조함을 리뷰로 확인. — 관찰 증거(문서 정합 inspection), red-first 비대상.
- **depends-on**: **T1–T4 착지 후**(불변식 정련은 구현이 실제로 격리를 실현한 뒤에 문서화). ADR-0020: intent 완화는 사용자 소유(이미 승인됨) — 이 편집은 방법의 기록.
- 근거: ADR §관계(정련/부분 supersede) + 설계 §8 거버넌스.

### T9-PRIMARY · (주 트랙) build-less tree-sitter 정밀 spine 개선 — name-based over-matching 축소

- **주경로 표기**: 정밀도의 **1급 경로**이며 palimpsest가 소유한다. 아래 T10(CodeQL)의 **선행이자 상위**다 — CodeQL 없이도 test-impact 필요를 푸는 경로이므로 CodeQL 트랙보다 **먼저** 착지시킨다.
- **change-target**: `src/palimpsest/extract/*.py`(현행 `java.py` 등). 현행 name-based over-matching(`CALLS`가 호출을 그 단순명을 가진 **모든** 메서드에 연결)을 tree-sitter `tags.scm` + `locals` 스코프 해소 + receiver-type 휴리스틱으로 좁힌다. build-less·전이력 균일(임의 git 체크아웃, 빌드 불요) 유지. 다언어로 확장 가능한 구조(언어별 `tags.scm`/`locals` 쿼리 분리).
- **verification**:
  1. **정밀도 테스트**: fixture에서 현행 name-based `CALLS`의 over-matching(단순명 충돌 시 잘못 연결된 엣지) 대비 **줄어든 엣지 집합**을 단언하는 테스트. 개선 전 엣지 수 > 개선 후(정확한) 엣지 수를 fixture로 고정.
  2. **다언어 노트**: 최소 1개 언어(현행 Java)로 착지하되, `tags.scm`/`locals` 쿼리가 언어별로 분리되어 다언어 확장 가능함을 구조로 확인.
- **depends-on**: 없음(독립 주 트랙, First slice와 병렬 가능). **T10(CodeQL)보다 먼저.**
- **red-first**: 예 — over-matching 축소는 코드 동작. 개선 전 over-match를 잡는 실패 테스트(잘못 연결된 엣지 존재 assert) → tags.scm/locals/receiver-type 해소 구현 → green.
- 근거: ADR §결정6(주 in-house tree-sitter spine); 설계 §6.2(1); design-notes D §0 pain(test-impact).

### T10 · (보조 트랙, 옵션) CodeQL HEAD-only overlay — security/taint Risk + 선택적 정밀 부스트

- **보조 표기**: 이 트랙은 **선택적 보조(auxiliary)**이며 정밀 spine(T9-PRIMARY)의 의존이 **아니다**. curate 축과 같은 격리-생산자/git-물질화 패턴을 공유하는 독립 슬라이스로, curate(T1–T4)·T9-PRIMARY 착지에 의존하지 않고 병렬 가능하나 **주경로가 아니다**. 지금은 드롭 가능하며, 결정론적 security-Risk 생산자가 실제로 필요해질 때 채택한다. 세부 edge_kind 표기 규칙은 CodeQL 슬라이스에서 확정(현재 open).
- **역할(니치)**: 빌드 가능한 HEAD에서 ① **security/taint Risk 산출** — build-less가 대체 못 하는 **유일한 역할(interprocedural taint)**, 이것이 채택 이유 ② 선택적 HEAD 정밀 부스트(virtual dispatch, dataflow). spine이 이미 build-less 정밀을 제공하므로 ②는 부가이고, 채택의 핵심 근거는 ①이다.
- **change-target**: 신설 CodeQL 생산자(위치는 CodeQL 슬라이스에서 확정; curate와 동형으로 격리 + optional-dep). CodeQL을 **빌드 가능한 HEAD에서만** 실행 → findings를 git-SoT에 물질화 → 기존 inferred/deterministic 로더로 적재. 이력 spine은 build-less tree-sitter 유지(CodeQL을 이력 엔진으로 쓰지 않음). `extracted_by:'codeql'` provenance 마커 + coverage-asymmetry(HEAD-only) 정직 표기.
- **verification**:
  1. HEAD findings(특히 security/taint Risk)가 git-물질화 후 로더를 통과해 provenance 마커를 갖고 적재됨.
  2. **coverage-asymmetry 정직성**: 회상이 "CodeQL 엣지는 HEAD-only, tree-sitter 엣지는 전이력"을 구분해 표기함을 확인.
  3. 이력 backfill 균일성(tree-sitter spine)이 CodeQL 추가로 깨지지 않음(기존 backfill 테스트 green).
- **depends-on**: 없음(별도 보조 트랙). spine이 정밀 주경로이므로 이 트랙 없이도 정밀 spine은 성립. curate 패턴(T1–T3) 착지가 참조 구현이 되면 재사용 가능하나 필수 아님.
- **red-first**: 예(적재/표기는 코드 동작) — CodeQL 슬라이스에서 세부 확정.
- 근거: ADR §결정6(보조 옵션 CodeQL, 니치=security-Risk); 설계 §6.2(2), §7.3 SOFT(C-Q2/C-Q5 open).

---

## 의존 그래프 요약

```
First slice (Candidate B 종단):
  T1(curate 모듈 + curate optional-dep)
    └─▶ T2(물질화 CLI)  ──▶ T3(기존 로더 배선)  ──▶ T4(격리 probe 확장, E1)
                                        └─▶ T6(VG2 재구축 결정론, E3)
    └─▶ T7(provenance 정직성)

Later / 병렬:
  T5(VG1 ADR 전수 스윕)              — 독립
  T9-PRIMARY(tree-sitter 정밀 spine) — 독립 주 트랙, T10보다 먼저
    └─▶ T10(CodeQL HEAD-only 보조)   — 독립 보조(옵션), spine의 의존 아님
  T8(ADR-20260701 supersede 편집)    — T1–T4 착지 후
```

## 미해결 질문

- CodeQL 슬라이스의 edge_kind 표기 규칙(`C-Q5`), 이력 spine 추출기(`C-Q1`), 직접 실행 vs DITTO 소비(`C-Q2`)는 T10(CodeQL 보조 트랙) 진입 시 확정 — 이 백로그 범위 밖(design-notes 개방 질문).
- T5(VG1)에서 5개 ADR 중 좁혀야 할 문장이 발견되면, 그 정련은 T8 확장 또는 해당 ADR별 후속 항목으로 분기(현재는 "발견 시 분기"로 열어 둠).

## VG1 후속 — supersede footprint 확장 (검증 노드 발견, 2026-07-06)

미독 5개 ADR 전수 스윕 결과(reexam-verify): 어떤 ADR도 in-process 생성을 **영구 금지하지 않음**(narrowing 안 막힘, 설계 핵심 판단 확증). 단 T5/T8이 supersede/재론 범위에 다음을 반드시 포함해야 한다:
- `ADR-20260703-branch-scoped-node-identity.md:56` — "provider-free 완화 시 이 ADR과 ADR-20260701 provider-free 불변식을 **함께 재론**한다"고 스스로 명시 → narrowing 시 joint 재론 대상.
- `ADR-20260702-communityreport-load-contract.md:61` — "별도 결정, ADR-20260701과 동일 입장: 현재 hard invariant 유지" → 20260701 narrowing이 전파되므로 상태 갱신 필요.

→ T8(ADR-20260701 §결정1 supersede)은 이 두 ADR의 cross-reference도 함께 갱신하도록 확장한다.
