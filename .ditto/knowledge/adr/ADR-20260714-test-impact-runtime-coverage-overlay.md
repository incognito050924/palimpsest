# ADR-20260714-test-impact-runtime-coverage-overlay — test-impact 정확도의 런타임 커버리지 보조 overlay: build-less·host-neutral·전이력 균일 3불변식을 producer-중립 load로 보존

- 식별자: `ADR-20260714-test-impact-runtime-coverage-overlay` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-14
- work item: wi_260714v6m (issue #19 항목②)

## 맥락

test-impact 축의 1급 default는 정적 전이 CALLS 역추적이다(`recall_test_impact`, `recall_changeset_impact`). 이는 **근본적 하한**이다 — reflection/DI/polymorphic-dispatch로 도는 test caller는 정적 콜그래프에 보이지 않는다. 코드가 이 한계를 `src/palimpsest/recall/graphrag.py`의 `_STATIC_LOWER_BOUND_GAP` 상수로 정직 표기한다("static CALLS is a lower bound … completeness is not claimed").

정적 하한이 실무에서 불충분하다는 **정확도 요구**가 실재함을 사용자가 확인했다(issue #19 항목② — 유예 승격 트리거). 이를 잡는 유일한 ground truth는 실제 실행 커버리지(build+run)다. 그러나 build+run 의존은 palimpsest의 build-less·전이력 균일·host-neutral 불변식(ADR-20260706, ADR-20260712)과 표면상 충돌한다. 이 ADR은 그 충돌을 예외 없이 해소한다.

## 결정

### 결정 1 — ADR-20260706 §결정6 보조-overlay 패턴의 test-impact 축 적용 (새 불변식 예외 아님, 상속)

런타임 커버리지는 `ADR-20260706 §결정6`이 콜그래프-정밀/taint 축에 이미 확정한 **"빌드 의존 보조 엔진 = 격리-생산자 / HEAD-only overlay"** 패턴을 test-impact 축에 그대로 **적용**한 것이다. 새 결정이 아니라 상속이다. CodeQL이 콜그래프 정밀/taint의 보조 overlay이듯, 런타임 커버리지는 test-impact 정확도의 보조 overlay다.

불변식이 **보존**되는 이유(예외가 아닌 근거):

- **build-less(palimpsest 기준) 보존** — palimpsest는 build+run을 직접 돌리지 않는다. producer(호스트 CI/개발자)가 palimpsest 밖에서 build+run+coverage를 수행하고 **coverage 산출물을 git-tracked SoT로 물질화**한다. palimpsest는 그 산출물만 **producer-중립 `load` 계약**(ADR-20260712 §결정1(c) — producer-중립 load)으로 흡수한다.
- **host-neutral 보존** — producer-중립 load. coverage 산출물이 없는 환경에서는 그냥 부재(옵션·droppable). 특정 호스트 결합 없음(ADR-20260712 §결정1(c) 역방향 종속 금지 준수).
- **전이력 균일 보존** — 정밀 spine(tree-sitter)이 전이력을 균일 담보하고, coverage overlay는 **HEAD-only**로 격리된다(ADR-20260706 §결정6: "build-dependency는 HEAD-only로 격리하고 전이력 균일성은 tree-sitter spine이 담보한다").

### 결정 2 — provenance/edge_kind 분리 (정적 채널 대체 아님, 증강). edge_kind 3값화

런타임 커버리지 overlay는 정적 CALLS test-impact 채널을 **대체하지 않는다**. 반드시 provenance/edge_kind로 분리해 정적 default를 **증강**한다. coverage로 도출된 엣지는 **`edge_kind="runtime"`** 으로 표기한다.

edge_kind는 현재 2값 이진이다(`deterministic` 구조층 / `inferred` 의미층 — `src/palimpsest/ir.py`의 `EDGE_KIND_*` 상수, schema로 강제되는 no-laundering 분리). 런타임 coverage는 **정적분석도 외부추론도 아닌 관측된 실행 사실**이라 3번째 범주다. 따라서:

- `EDGE_KIND_RUNTIME = "runtime"` 를 `ir.py`의 `EDGE_KIND_*` 상수 옆에 추가하고, 이진 규정 주석을 3값(구조/추론/관측)으로 정련한다.
- `deterministic + inferred == total` 형태의 fixture partition 단언이 runtime 엣지를 실은 그래프를 가로지르지 않음을 확인한다(현재는 coverage 로더가 안 도는 fixture라 무저촉).

정적 test-impact 회상 item은 오늘 `relation=CALLS`만 실어 구분한다(edge_kind 필드 없음). 런타임 overlay는 test-impact 계열 최초의 **엣지-판독** 채널이므로 `relation=COVERS` + `edge_kind="runtime"` 으로 분리 표기한다.

### 결정 3 — COVERS 엣지: 방향·granularity·물질화 형태

`ADR-20260706 §결정6` 선례(findings를 git-SoT 물질화 후 멱등 로더로 적재)와 CALLS_API 템플릿(기존 노드 간 새 엣지, `REL_TYPES` 밖, 전용 로더가 유일 생산자, backfill 루프 미포함 — `src/palimpsest/kg/calls_api.py`의 로더, `src/palimpsest/ir.py`의 `CALLS_API` 상수에 명문화된 "REL_TYPES 밖" 불변식)을 따른다.

- **엣지**: `(:Method{is_test:true}) -[:COVERS {edge_kind:"runtime", source_commit}]-> (:Method)` — **test method가 production method를 실행-시 커버**. 방향 = test→production("test covers code"). granularity = **Method**(정적 CALLS 채널과 동일 granularity라 대칭 증강). `COVERS` constant를 `ir.py`에 추가하되 `REL_TYPES` 밖에 유지(CALLS_API처럼 generic writer·backfill이 못 만들도록 — `ir.py`의 `CALLS_API` 주석에 명문화된 "DELIBERATELY absent from REL_TYPES" 불변식).
- **물질화**: 정적 test-impact는 회상-시-계산으로 유지한다(materialize 아님 — `recall_test_impact`가 회상 시 정적 CALLS를 역추적, `graphrag.py`). 런타임 overlay만 §결정6 격리-생산자 패턴상 물질화한다(producer 산출물 → git-SoT → 멱등 `load_coverage` 로더 → COVERS 엣지). endpoint(test id·production id) 선(先)해석, 미해결은 reject(entity-atomic, silent no-op 금지 — `kg/calls_api.py`/`kg/risk.py` 선례).

### 결정 4 — producer-중립 per-test coverage payload

런타임 test-impact overlay는 "어느 **테스트**가 어느 production 코드를 실행-시 커버했나"를 요구하므로, 집계 coverage(총 라인 커버리지)가 아니라 **per-test attribution**(pytest `--cov-context`, jacoco per-test session, c8 per-test 등)이 필요하다. palimpsest는 이를 직접 산출하지 않는다(build-less) — producer가 밖에서 산출해 **git-SoT dir-of-JSON**으로 물질화하고, palimpsest는 producer-중립 `load` 계약으로 흡수한다.

payload 스키마(정규화):

```
{ test_qualified_name, covered: [production_qualified_name, ...], source_commit }
```

특정 coverage 도구/포맷에 결박하지 않는다(ADR-20260712 §결정1(c) producer-중립; lcov/jacoco → 이 정규 JSON 변환은 producer 책임). per-test attribution 필요를 이 load 계약에 명문화한다.

### 결정 5 — HEAD-only 격리 + 분리 recall 채널

- **HEAD-only**: `load_coverage`를 `backfill.py` per-commit 루프에서 호출하지 않고, ingest 후 HEAD projection에 1회 적용한다. 근거·선례: `backfill.py`는 inferred/runtime 로더를 임포트조차 하지 않는다(`augment_communities/create_constraints/ingest/ingest_modifies`만 임포트) → 인퍼드/관측 엣지는 구조적으로 HEAD-only. CALLS_API도 backfill 밖 별도 CLI step으로 적용되는 동형 선례. 엣지 `source_commit` = 측정된 HEAD commit. 전이력 균일 spine(tree-sitter, MERGE-dedup)은 불변.
- **회상**: COVERS 엣지를 역방향 판독하는 새 `recall_*` 채널(production method → 커버하는 test method), `relation=COVERS`·`edge_kind="runtime"` 태그. 정적 채널의 `_STATIC_LOWER_BOUND_GAP`은 1급으로 유지하고 overlay는 그 위 증강. `changeset-impact` 4-touch 배선 패턴(graphrag fn → recall `__init__` export → cli subparser + `_cmd` + `_print`)을 따른다.

## change_condition (철회 조건)

- ADR-20260706 §결정6과 동형: coverage overlay는 **droppable**. 정확도 요구가 사라지거나 정적 spine이 충분해지면 드롭 가능.
- producer-중립 load 계약을 깨고 palimpsest 코어가 특정 호스트/build 산출물에 **역방향 종속**하게 되면 이 결정을 재검토한다(ADR-20260712 §결정1(c) 위협 → 집행 강화의 결과).

## 대안 / 기각

- **palimpsest가 build+run을 직접 오케스트레이션**: 기각. build-less·host-neutral 불변식을 실제로 깬다. producer-중립 load(밖에서 물질화 → 흡수)로 불변식 보존이 가능하므로 불필요.
- **정적 채널을 런타임으로 대체**: 기각. 정적이 build-less 1급 default이고 `_STATIC_LOWER_BOUND_GAP`으로 하한을 정직 표기한다. 런타임은 옵션 보조 overlay로 provenance 분리해 증강만 한다.
