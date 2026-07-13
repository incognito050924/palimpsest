# palimpsest 설계 노트 (living backlog)

> **성격**: 살아있는 설계 노트/요건 백로그. **ADR 아님**(확정 결정 아님), 권위 아님 — 코드가 SoT다(§4-11).
> 결정 후보·설계 요건·미해결 질문을 **주제별로** 쌓는다. 여기 "사실"은 `파일:라인`으로 코드에서 확인한 것,
> "제안"은 아직 결정 안 된 설계 판단이다. 계속 갱신·변경한다. 특정 주제가 확정되면 그 부분만 ADR로 승격한다.
>
> 최초 작성: 2026-07-05 · 상태: **탐색(exploration)**

## 주제 색인
- **[A] 다언어 온톨로지 확장** — Kotlin/Python/JS·TS 등 함수-우선 언어 지원 (상태: 탐색)
- **[B] Summary 커버리지·응집 검증** — 요약이 코드를 다 담았나 / 코드가 한 가지만 하나 (상태: 탐색)
- **[C] 구조 추출 정밀도** — source-only AST(현재) vs semantic 분석(CodeQL)·외부 producer 소비 (상태: 탐색)
- **[D] 테스트 코드 모델링 & 테스트 임팩트** — 테스트/프로덕션 구분 + test→target 관계 + 변경→영향 테스트 회상 (상태: 탐색)
- **[E] 브랜치 모델링** — branch를 property(현재)로 vs Branch 노드로 reify (진입점·계보). 성능은 인덱스로 (상태: 탐색)
- **[F] 코드베이스 품질 이해·정규화 substrate** — 코드 품질을 이해·정규화 패턴 제공, 구조를 지속 가꾸기(pre-compute 질의가능 팩트층). ditto#9의 palimpsest측 동기 (상태: 탐색 · seed)

> 새 주제는 여기에 한 줄 추가하고 아래에 `## [X] 제목` 섹션을 붙인다.

---

# [A] 다언어 온톨로지 확장

## 0. 이 주제가 다루는 문제

현재 IR/KG 온톨로지는 **"모든 코드는 클래스 안에 있다"는 Java 가정**에 묶여 있다.
Kotlin·Python·JS/TS처럼 **파일/모듈 최상위에 함수·변수를 선언**하는 언어에선 그대로는 부적절하다.
여기서 무엇이 언어중립이고, 무엇이 Java 종속이며, 어떻게 일반화할지를 모은다.

---

## 1. 언어중립이라 재사용되는 것 (사실)

git·그래프 하부 기반은 언어를 안 탄다. 다언어에서도 그대로 쓴다.

- `Provenance`·Episode·`MODIFIES`·churn/co-change — git 기반(`extract/provenance.py`), 언어 무관.
- branch-scoped identity(`ir.py:32-43`), deterministic/inferred 분리, MERGE 멱등, 회상 채널, git=SoT / Neo4j=projection.
- **extract 계층이 언어별 플러그 지점**(`extract/java.py`는 tree-sitter-java 하나). 새 언어 = `extract/<lang>.py` 추가. IR이 공유 계약(target).

→ 결론: **하부는 그대로, 노드 온톨로지만 확장 대상.**

---

## 2. Java-class 가정이 박힌 곳 (사실 — 코드 확인)

| # | 가정 | 근거 | 다언어 영향 |
|---|---|---|---|
| F1 | 노드 종류에 top-level 함수·변수가 없음 (`REPO/PACKAGE/FILE/CLASS/METHOD/COMMUNITY`뿐) | `ir.py:46-54`, `NODE_LABELS` `ingest.py:55` | 최상위 함수/모듈 변수를 담을 노드 없음 |
| F2 | METHOD는 CLASS body에서만 생성 | `java.py:174,182` (`_method_decl`은 `_type_decl`에서만 호출) | 클래스 없는 함수 = 노드 없음 |
| F3 | VARIABLE/FIELD 노드 없음 (Java field조차 DEPENDS_ON 파생용) | `java.py:177` | 모듈 상수·export 표현 불가 |
| F4 | Community 멤버·연결이 CLASS 하드코딩 | `community.py:57,69,72,88` (`nodes_of(CLASS)`) | 함수-우선 코드는 클러스터링에서 누락 |
| F5 | Community의 CALLS 판단이 "메서드→선언 클래스"로 lift, 클래스 없으면 버림 | `community.py:75-78` (`if cs and cd`) | top-level 함수 간 CALLS가 그룹핑에 안 쓰임 |
| F6 | PACKAGE·qualified_name이 Java식(`pkg.Class#method(ParamTypes)`) | `java.py` (`_package_fqn`, `_param_types`) | JS/TS는 package 선언 없음·오버로드 없음 등 규약 상이 |

---

## 3. 품질 저하 지점 (제안/판단)

- **P1. DEPENDS_ON이 동적 타입 언어에서 희박**: 지금 DEPENDS_ON은 필드·파라미터 **타입 참조** 기반(`java.py:231-244`). Python/JS는 정적 타입이 거의 없어(덕 타이핑) 이 파생이 빈약 → IMPORTS·CALLS에 더 의존해야.
- **P2. 이름 기반 CALLS 해석의 부정확성 심화**: 이미 coarse(동명 오버매칭)한데, 일급 함수·동적 디스패치·몽키패치가 흔한 JS/Python에선 더.

---

## 4. 핵심 문제 상세 — 클래스 없는 함수 간 CALLS를 Community가 못 씀 (F5)

Community는 CALLS를 직접 안 쓰고 클래스로 올려서 본다:
```python
# community.py:75-78
for e in ir.edges_of(CALLS):
    cs, cd = m2c.get(e.src), m2c.get(e.dst)   # 메서드 → 선언 클래스
    if cs and cd and cs != cd:                 # 클래스 없으면 None → 버림
        pairs.add(frozenset((cs, cd)))
```
`m2c`(`_class_of_method`, `:57-63`)는 `CONTAINS` 중 **src가 Class**인 것만. → top-level 함수는 `m2c.get(func)=None` → CALLS 쌍 통째 탈락, 멤버(`nodes_of(CLASS)`)에도 없음.

**중요**: 엣지 `(:Function)-[:CALLS]->(:Function)`는 멀쩡하다. 문제는 **그룹핑이 그 엣지를 못 쓰는 것**뿐.

---

## 5. 제안 — "unit 일반화" (제안, 미확정)

### 5-1. 엣지는 그대로, 끝점 노드만 존재시키기
CALLS 스키마 불변. extractor가 top-level 함수를 노드로 emit하되 부모를 Class가 아니라 **File(모듈)**로 CONTAINS:
```
(:File)-[:CONTAINS]->(:Function)      # 클래스 없이 파일이 컨테이너
(:Function)-[:CALLS]->(:Function)     # 엣지는 지금 그대로
```
노드 종류는 신규 `FUNCTION` 추가, 또는 METHOD를 "File 밑에도 올 수 있는 것"으로 완화 — **미정(§7 Q1)**.

### 5-2. Community의 lift를 일반화 (진짜 고칠 곳)
"메서드→클래스"를 **"코드 단위 → 감싸는 그룹핑 컨테이너"**로:

| 코드 단위 | 컨테이너(=Community 멤버) |
|---|---|
| 클래스 메서드 (Java/Kotlin/Python 메서드) | 선언 **Class** (현행 유지) |
| top-level 함수 (Python/JS/TS/Kotlin) | 그 **File/Module** |

즉 `_class_of_method` → `_unit_of(symbol)`("Class 있으면 Class, 없으면 File/Module"), 멤버 집합 `nodes_of(CLASS)` → **`nodes_of(CLASS) ∪ Module(File)`**.
결과: Python 모듈 A 함수가 모듈 B 함수를 CALLS → **모듈 A↔B** 연결 → 모듈들이 커뮤니티로 묶임. Java는 그대로. 혼합 코드베이스도 한 그래프에서 처리.

### 5-3. 입도(granularity) 정책 — 택1 필요 (미정)
- **모듈/파일 단위 (현재 기운 쪽)**: Class 자리에 File/Module. GraphRAG 요약 목적에 맞게 적당히 coarse.
- **함수 단위**: lift 없이 함수 콜그래프 직접 연결 요소. 충실하나 잘게 쪼개져 요약 앵커로 과함.
- **혼합/계층**: 클래스 있으면 Class, 없으면 Module (위 표).

---

## 6. 확정 시 영향 범위 (구현 지도 — 참고)

- `ir.py`: 노드 종류 상수(+`FUNCTION`, 선택적 `VARIABLE`), `NODE_LABELS` 확장.
- `extract/<lang>.py`: 언어별 신규 추출기(tree-sitter grammar). top-level 함수/변수 emit, qn·PACKAGE 도출.
- `kg/community.py`: `_class_of_method`→`_unit_of` 일반화, 멤버 대상 확장(`:57-88`).
- `recall/graphrag.py`: `recall_community`는 멤버 종류에 무관하면 대체로 그대로.
- 테스트: 언어별 fixtures + community 일반화 케이스(연결/고립/혼합).

---

## 7. 미해결 질문 (Open Questions — 여기 계속 추가)

- **Q1**: top-level 함수를 신규 `FUNCTION` 노드로 vs METHOD를 File-부모 허용으로 완화? (온톨로지 최소성 vs 명시성)
- **Q2**: Community 입도 정책 — 모듈 단위 / 함수 단위 / 혼합 중 무엇을 기본으로?
- **Q3**: 모듈 변수·export(`VARIABLE`)를 1급 노드로 넣을 가치가 있나? (질문에 실제로 쓰이나 — 없으면 안 만든다, §4-3)
- **Q4**: 동적 타입 언어에서 DEPENDS_ON을 포기/약화하고 IMPORTS+CALLS 중심으로 갈지?
- **Q5**: 어느 언어를 먼저 지원? (Python / TS / Kotlin — 대상 코드베이스 기준)
- **Q6**: qualified_name 스킴을 언어별로 어떻게 통일/구분? (오버로드 유무, 확장함수, 익명/람다)

---

## 8. 언어별 정련 (실현됨 — 권위는 코드)

> 아래는 실현된 추출기의 결정을 탐색노트에 반영한 것이다. **동작의 SoT는 코드**(각 `extract/<lang>.py` docstring + `tests/extract/test_extract_<lang>.py`)이며, 이 표는 drift할 수 있는 요약이다(§4-11).

### 8-G. Go (issue #11 — `extract/go.py`)

Go는 클래스가 없어 de-Class 하부(commit 138db70)의 대표 사례다. 미해결질문 해소:

- **Q1**(FUNCTION vs METHOD 완화): 신규 `FUNCTION`으로 확정. Go grammar가 `function_declaration`(리시버 없음)과 `method_declaration`(리시버 있음)을 네이티브로 구분하므로 부모-컨텍스트 추론이 아니라 **노드 타입 + 리시버**로 판별. top-level func → File CONTAINS Function, receiver method → 리시버 타입 Class CONTAINS Method.
- **Q6**(qn 스킴): Go의 package 정체성은 **디렉토리**(import path의 모듈-이하)다. Package = repo-relative 디렉토리, 콜러블은 이를 prefix — `Function: <dir>.name(paramTypes)`, `Method: <dir>.RecvType#name(paramTypes)`, `Class: <dir>.Type`. Go는 오버로드가 없어 paramTypes는 유일성엔 불필요하나 언어 공통 콜러블-정체성 스킴 일관성을 위해 유지.
- **type_spec → Class**: struct·interface·named type 모두 Class(Java 선례: interface/enum/record → Class). Class는 메서드 컨테이너·Community 그룹핑 단위·DEPENDS_ON 대상 3역할. 모든 리시버 타입은 같은 패키지에 선언되므로(Go 규칙) 파일이 갈려도 Method의 Class 컨테이너가 dangling하지 않음(샘플 122/122 검증). **제네릭 리시버** `func (s *Stack[T])`는 base 타입 `Stack`으로 정규화(포인터·타입 인자 벗김) — 타입 파라미터 `T`로 새면 컨테이너가 dangling하고 서로 다른 제네릭 타입의 동명 메서드가 `pkg.T#m`으로 병합됨(회귀 테스트로 고정).
- **interface 메서드 시그니처**: 별도 노드로 방출하지 않음 — 본문·호출부가 없어 콜그래프·그룹핑에 기여 없음(§4-3).
- **type alias**(`type A = B`, `=` 있음)는 Class로 방출하지 않음 — 새 타입이 아니라 기존 타입의 동의어이고 메서드는 원 타입에 붙으므로(§4-3). 방출 대상은 새 named 타입을 만드는 `type A B`(defined type)만.
- **DEPENDS_ON**(Q4 정련): Go는 정적 타입이므로 방출(ADR-20260713 결정1). struct 필드·함수/메서드 파라미터·결과 타입의 참조 타입명 → Class에 이름 기반 매칭. top-level 함수의 참조는 File/Module을 src로(de-Class 일반화). builtin·외부 타입은 매칭 Class 없으면 방출 안 함(지어내지 않음).
- **Q3**(VARIABLE): Go 패키지-레벨 `var`/`const`는 노드로 **방출하지 않음** — 회상(`recall/graphrag.py` `_NODE_LABELS`)·Community 어느 소비자도 Variable을 쓰지 않아 §4-3에 따라 만들지 않는다.
- **is_test 마커**: 범위 밖(recall_test_impact 채널 = issue #7). `_test.go`의 함수·메서드는 일반 Function/Method로 방출하되 `is_test`는 세팅하지 않음.
- **vendor/**: Go 벤더링 third-party 코드는 추출에서 제외(ecmascript vendored-exclusion 경계와 동형).

---

# [B] Summary 커버리지·응집 검증

## 0. 문제

Summary는 코드에 대한 외부 요약이다(`Summary ─SUMMARIZES→ 코드`, `ir.py:74-75`). 그런데 두 방향의 품질이 있다:
- **Faithfulness** — 요약의 주장이 코드로 **뒷받침되나** (claim → code). ✅ 있음.
- **Coverage/응집** — 요약이 코드의 실제 책임을 **다 담았나** / 코드가 한 가지만 하나 (code → claim). ❌ 없음.

예: "CommuteController는 출퇴근 CRUD 담당"은 휴가 코드가 상당수 있어도 **참**이라 faithful 통과. 하지만 **불완전**(휴가 책임 누락)하고, 애초에 그 클래스가 **책임 혼재(낮은 응집)**일 수 있다. 이 축이 지금 안 잡힌다.

## 1. 지금 있는 것 (사실)

- `semantic_verdict = {"verdict": "faithful"|"unfaithful"|"unverified", judge, model}` (`ir.py:49-51`). 외부 judge(#1 내용검증)가 **faithfulness**를 판정 → 적재·회상 annotate(거부 아님, `test_recall_summaries.py:136-150`). palimpsest는 판정 안 함(provider-free), 하네스는 ditto측(DESIGN §6 #1 ◐ 부분 shipped).
- **한계**: faithfulness는 "틀린 것"은 잡아도 "빠뜨린 것"은 못 잡는다.

## 2. 지금 없는 것 (사실)

- coverage/응집 지표는 코드에 없음(grep 무결과). code→claim 방향(요약이 코드를 다 덮나)은 미모델링.
- 구조층은 **Class 단위**라 클래스 내부를 책임별로 쪼개지 않음 — "이 클래스가 두 가지를 한다"는 구조적으로 미검출(Community도 Class-level).

## 3. 계산 가능한 잠재 자료 (제안)

- 각 `SummaryClaim`이 `source_refs`(근거 코드 span)를 가짐(`ir.py` SummaryClaim). → **대상의 CONTAINS 자식 중 어떤 claim source_ref로도 안 잡힌 노드 = 커버리지 갭**을 **결정론적으로 계산 가능**(LLM 불필요). stale(시간축)처럼 **coverage flag(범위축)**를 detect-only로 얹을 여지.

## 4. 어디에 표현할지 — 후보 (미정)

- **B-후보1 (권장, provider-free)**: 구조적 coverage 지표 — claim source_refs ∪ vs 대상 자식 → 미커버 노드 계산·flag. LLM 불필요.
- **B-후보2**: "응집도 낮음/책임 혼재"를 외부 judge가 `Risk`로 표현(판단 필요).
- **B-후보3**: 요약 **입도**를 메서드별로 → 커버리지가 자연히 per-method, 빠진 메서드 = 빠진 요약(구조적으로 보임). 클래스 요약은 롤업.

## 5. 미해결 질문 (Open Questions)

- **B-Q1**: coverage를 detect-only 구조 지표로(후보1) vs 외부 judge Risk로(후보2) vs 입도 정책으로(후보3) 풀지?
- **B-Q2**: "커버리지 갭"의 기준 — 대상 직속 메서드만? CONTAINS 전이 포함? 사소한 getter/setter 제외?
- **B-Q3**: 응집도(클래스가 여러 책임)를 1급으로 표현할 가치가 있나, 아니면 Risk/요약 입도로 충분한가?

## 6. 관련 축 — 신선도(freshness)와의 구분

Summary 품질엔 세 축이 있고 서로 다르다. 헷갈리지 않게 정리:

| 축 | 질문 | 방향 | 상태 |
|---|---|---|---|
| **Faithfulness** | 주장이 코드로 뒷받침되나 | claim → code | ◐ 있음(`semantic_verdict`, 외부 judge) |
| **Freshness (stale)** | 요약이 결박된 뒤 코드가 바뀌었나 | 시간축 | ✅ shipped(detect-only) |
| **Coverage/응집** | 요약이 코드를 다 담았나 / 코드가 한 가지만 하나 | code → claim | ❌ 없음(이 주제 B) |

**Freshness는 이미 shipped**라 이 백로그의 대상은 아니지만, coverage와 자주 혼동되므로 사실만 기록:
- 적재 시 `code_bound_at` = 대상 코드의 `committed_at`으로 결박(`summary.py:370`). 코드가 새 커밋으로 재-ingest되면 `committed_at`이 갱신돼 둘이 어긋남.
- 회상 시 `_stale(code_bound_at, committed_at)`이 비교 → 다르면 `stale=True` **flag만 달아 반환**(삭제·숨김 아님, `graphrag.py`). **순수 비교, LLM·재생성 없음**(provider-free).
- **재생성은 외부 몫**: stale 신호가 트리거, 새 payload를 `load`하면 풀림. palimpsest는 self-healing 아님 — staleness를 **관측 가능**하게 만들 뿐.
- 한계: **전수 스윕 아님**(회상된 요약마다 lazy 비교) · **coarse**(코드 노드 재커밋만 감지, 그 변경이 요약을 실제로 무효화했는지는 판정 못 함 — 그건 faithfulness/coverage 축).

→ 정리: **freshness=시간 drift(있음), coverage=범위 누락(없음, 주제 B), faithfulness=내용 오류(부분).** 세 축은 독립이며 서로 대체하지 않는다.

## 7. 관계 — 주제 F(#1)와의 소유권 경계 (중복정의 방지)

#1(주제 F, 코드품질 substrate)과 **합치지 않는다.** 고도가 다르다: F는 정체성급 상류(F-Q4가 5기능 모델을 게이팅, 정밀 spine C/T9 선행)이고 B는 거의 착수 가능한 좁은 슬라이스다. 합치면 B가 F의 교착에 끌려간다(§4-3, IntentContract).

겹치는 축은 **F-Q5(품질 측정 기준) ↔ B의 coverage/응집** 하나뿐이며, 두 번 정의하면 drift다. 따라서 **소유권 계약**:

- **B가 coverage/응집 facet의 SoT를 소유**한다(구체 정의·갭 기준·구현). F-Q5는 이 facet을 **재정의하지 않고 B에 위임**한다 — F-Q5는 facet 열거(응집도/콜그래프 지역성/명명 일관성…)만 하고 coverage는 여기를 가리킨다.
- **B는 F의 첫 실증 파일럿**이다: B-후보1(claim source_refs ∪ vs CONTAINS 자식 → 미커버 노드 flag)과 F-후보1(KG projection 위 결정론적 품질 신호)은 **동형**(둘 다 provider-free·detect-only). B를 먼저 구현하면 "KG 위 품질 신호"의 구체 모양이 나와 F-Q1/F-Q3를 경험적으로 탈위험화한다.
- 경계: B 단독은 구조층이 Class-level이라 **coverage(누락) facet만 지금 깨끗이 성립**하고, 응집(클래스 내부 다책임)은 온톨로지 일반화(주제 A) 없이는 반쪽이다.

---

# [C] 구조 추출 정밀도 — source-only AST vs semantic 분석 / 외부 producer

## 0. 문제

구조층(Class/Method/CALLS/DEPENDS_ON…)을 지금은 **source-only AST**로 뽑는다. 이름 기반 해석이라 부정확(오버·언더매칭, 주제 A P1/P2). 더 정확한 **semantic 분석(CodeQL 등)**으로 바꾸거나 보강할지.

## 1. 사실

- 현재: `extract/java.py` = **tree-sitter-java** (`java.py:12-13`). 즉 **이미 AST를 쓴다** — 선택지는 "AST냐 CodeQL이냐"가 아니라 "source-only AST(현재) vs 빌드 기반 semantic 분석".
- DITTO: **CodeQL 단일 엔진**(ditto ADR-0006 "정적 분석 엔진 통일"). memory-IR을 `extracted_by:'codeql'`로 산출(DependencyEdge/IMPORTS/symbols). LSP 감지도 있음.
- 선례(research, `docs/research/precompute-hugrag-kg.md`): **Glean**("재파싱이 아니라 색인된 사실을 질의"), **CPG/Joern**(정적층 온톨로지 출발점).

## 2. CodeQL의 이점과 비용

- **이점(정확도)**: 타입 해석·실제 콜그래프·상속/인터페이스·데이터플로우 → 주제 A의 P1/P2 정밀도 갭 직접 해결, 보안 Risk 감지에 강함.
- **비용(빌드 의존 — 핵심 충돌)**: CodeQL 정확도는 **빌드/컴파일 추적**에서 나온다. palimpsest 불변식과 정면 충돌:
  - **전 git 이력 backfill**: 옛 커밋은 빌드 안 됨(깨진 빌드·사라진 의존성·툴체인 drift). backfill은 `git archive`로 트리만 풀어 소스 파싱하는데 CodeQL은 컴파일 필요.
  - **임의 스냅샷 동작·결정론 재구축**: 빌드 환경 의존이라 git만으로 재현 안 닫힘.
  - **비용**: DB 빌드 스냅샷당 분 단위 × 수백 커밋 = 비현실적.
  - **언어 taxonomy 한계**: php/dart/lua 제외, Kotlin을 Java와 분리(ditto 테스트).

## 3. 결정 방향 (제안) — 교체 아닌 계층 분리

- **이력 spine(전 커밋, 빌드리스 필수)**: tree-sitter AST 유지. 이름 해석 개선이 필요하면 **SCIP·stack-graphs·Glean**(빌드리스 정밀 해석)이 CodeQL보다 여기 맞음.
- **정밀 overlay(빌드 가능한 스냅샷=보통 HEAD)**: CodeQL/LSP로 정확한 엣지 보강. 단 **이력을 못 덮음**.
- **coverage 비대칭 모델링**: "HEAD-only 정밀 CodeQL 엣지" vs "전이력 AST 엣지" — 둘 다 `edge_kind=deterministic`이나 커버리지·정밀도가 다름을 provenance로 구분.
- **외부 producer 소비 (유력)**: DITTO가 이미 CodeQL로 구조 팩트를 뽑으니, palimpsest가 그걸 **`load`로 흡수**(provider-free·적재 기반이라 정합). 재파싱 대신 정규화된 IR 수용.

## 4. multilang과의 관계 (주제 A 연결)

- 외부 정규화 producer(CodeQL/DITTO/Glean) 소비 시 **언어별 tree-sitter 파서 N개 손수 작성 부담이 크게 준다** — producer가 언어를 공통 어휘로 정규화.
- **그러나 주제 A의 온톨로지 일반화는 안 사라지고 매핑 계층으로 옮겨간다**: 정확한 다언어 팩트가 와도 palimpsest IR이 Class 중심이면 top-level 함수·모듈 변수를 못 담는다 → **FUNCTION 노드 + Community/CALLS/DEPENDS_ON de-Class화(A §5)는 여전히 필요.** producer는 "어떻게 파싱하나"를 풀지 "어떻게 표현하나"는 안 푼다.
- 빌드/이력 제약도 다언어에 그대로: CodeQL 다언어 정밀은 HEAD-only, 이력 spine은 언어별 빌드리스 필요.
- **재프레임**: 주제 A 비용이 "N개 파서 작성 + 온톨로지 일반화 + 빌드리스 spine" → **"외부 IR 매핑 + 온톨로지 일반화 + 빌드리스 spine"**. 파서 작성만 줄고, 온톨로지·이력 작업은 남는다.

## 5. 미해결 질문 (Open Questions)

- **C-Q1**: 이력 spine을 tree-sitter로 유지 vs SCIP/stack-graphs/Glean로 빌드리스 정밀 해석 도입?
- **C-Q2**: CodeQL overlay를 palimpsest가 직접 실행 vs **DITTO 산출을 소비**?
- **C-Q3**: HEAD-only 정밀 엣지 vs 전이력 AST 엣지의 coverage 비대칭을 `edge_kind`/provenance로 어떻게 구분?
- **C-Q4**: 외부 producer IR(DITTO Symbol/DependencyEdge) → palimpsest IR(Class/Method/CALLS…) 매핑 규칙? (주제 A의 unit 일반화와 함께 정의)
- **C-Q5**: CodeQL 유래 결정론적 Risk를 기존 inferred Risk와 어떻게 구분? (별도 `edge_kind`? provenance `extracted_by`? Risk 서브타입?) — no-laundering 불변식 정합 필수.
- **C-Q6**: Risk findings의 HEAD-only 커버리지(이력 미포함)를 회상에서 어떻게 정직하게 표기?

---

# [D] 테스트 코드 모델링 & 테스트 임팩트 분석

## 0. 동기 (사용자 실제 고통)

"코드는 고쳤는데 테스트가 깨지는 경우가 허다하다. 특히 **간접적으로 연결**되면, **관계가 멀면 에이전트가 관련 코드(테스트)로 인지조차 못 하는** 경우가 있었다." → 변경이 어떤 테스트에 영향을 주는지(test impact)를 그래프가 답해주면 좋겠다.

## 1. 현재 상태 (사실 — grep 확인)

**테스트는 IR에 표현은 되나 구분은 안 된다.**
- 온톨로지에 test/covers/verify 개념 **없음**(`ir.py` 노드 = REPO/PACKAGE/FILE/CLASS/METHOD/COMMUNITY뿐).
- extractor가 `src/test`·`@Test`·junit 등을 **구분 안 함**(`java.py` 무처리) — 테스트 `.java`도 평범한 Class/Method로 추출.
- `TESTS`/`COVERS`/`is_test` 엣지·속성 **없음**.
- 테스트↔프로덕션 링크는 **오직 `CALLS` 엣지(test method → production method)로만 암묵적**.

## 2. 갭 (사실 + 판단)

1. **테스트/프로덕션 구분 없음** — "테스트만"/"프로덕션만" 질의 불가.
2. **명시적 test→target(TESTS/COVERS) 엣지 없음** — "이 테스트가 저 코드를 검증"이 **다중 홉 CALLS로만** 암묵, 1급 아님.
3. **간접/먼 관계 미인지** — 이름 기반 CALLS라 `test → helper → production` 같은 먼 체인을 놓침 = 사용자가 겪은 바로 그 실패. (주제 C의 P2와 동일 뿌리)

## 3. 왜 주제 C(CodeQL)의 킬러앱인가

간접/먼 test↔code 인지는 **정밀 전이 콜그래프**가 있어야 풀린다. 이름 기반 AST는 먼 체인을 잃지만 CodeQL 실 콜그래프(+데이터플로우)는 정확·traversable. → **테스트 임팩트 분석이 CodeQL 도입의 강력한 정당화.** (주제 C ↔ D 상호 강화)

## 4. 제안 (모델링 방향)

- **D-1 테스트 마커**: Class/Method를 test로 분류 — 경로(`src/test/`·`*_test.py`·`*.spec.ts`)·애노테이션(`@Test`)·프레임워크 import(junit/pytest/jest). 싸고 가치 큼. `is_test` 속성 또는 TEST 서브타입.
- **D-2 `TESTS`/`COVERS` 엣지(1급)**: test→검증 대상. 도출 —
  - **정적**: 전이 CALLS(test → 도달 production) — 간접까지는 정밀 콜그래프(CodeQL) 필요.
  - **런타임**: 커버리지 맵(테스트 실행 → 커버 라인) — "실제로 도는" 것에 가장 정확하나 **테스트 실행 필요**(빌드+실행 의존, HEAD-only).
- **D-3 테스트 임팩트 회상 채널**: 변경(MODIFIES)에서 역방향으로 "이 코드를 전이적으로 커버하는 테스트" — MODIFIES 역추적 + test 필터. 에이전트가 놓치던 관련 테스트를 그래프가 찾아줌.

## 5. 미해결 질문 (Open Questions)

- **D-Q1**: 테스트 구분을 속성(`is_test`)으로 vs 별도 노드 서브타입(TEST)으로?
- **D-Q2**: `COVERS`를 정적 전이 CALLS로 도출(빌드리스 가능, 정밀도는 콜그래프 품질에 좌우) vs 런타임 커버리지로(정확하나 실행·HEAD-only)? 둘 다 두고 provenance로 구분?
- **D-Q3**: 테스트 임팩트를 회상 채널로(질의 시 역추적) vs `COVERS` 엣지로 미리 materialize? (churn/co-change처럼 bounded 필요)
- **D-Q4**: 다언어 테스트 프레임워크(junit/pytest/jest/…) 마커 규칙을 어디서 관리? (주제 A extractor별)

---

# [E] 브랜치 모델링 — property vs Branch 노드(reification)

## 0. 문제

`branch`가 지금은 **노드 property**(+ `branch_scoped_id`로 id에 접힘). Neo4j/GraphDB 특성상 **진입점**을 위해 `Branch` 노드로 reify하는 게 나을지 — **단 branch를 중요하게 자주 쓴다면**.

## 1. 사실 (grep 확인)

- branch = 노드 property + **정체성 차원**(`branch_scoped_id`, ADR-20260703-branch-scoped-node-identity). 필터는 `WHERE n.branch IN $branches`.
- **branch 속성 인덱스 없음** — `create_constraints`는 라벨별 **id uniqueness만**(`ingest.py:192-197`). branch-진입은 현재 인덱스 백업 없음(label scan).
- **Branch 노드는 명시적 out_of_scope**(`wi_260704gv7 intent.json:18`).
- 이미 **per-branch `CaptureManifest` 노드**(`capture:{key}\x1f{branch}`, `reconcile.py:150`) — 캡처 상태의 부분 reification(코드 진입 앵커 아님).

## 2. 두 갈래 (무게 다름)

- **① 속성 인덱스 (싼 답, 성능/진입점만)**: `CREATE INDEX ON :Label(branch)` → `WHERE n.branch IN $b`가 NodeIndexSeek. 프로젝트 기존 본능(wi_2607022ge: label-MATCH로 IndexSeek 전환). **성능만이면 Branch 노드 불필요.**
- **② Branch 노드 (성능 아닌 다른 가치)**: branch 메타데이터(tip·base·owner·created_at) 집 + **계보 1급화(`BRANCHED_FROM`/`MERGED_INTO`)** + reconcile 앵커.

## 3. 트레이드오프 (핵심)

- **풀 Branch 노드**(모든 plane 노드에 `ON_BRANCH` 엣지) = **엣지 폭발 → 슈퍼노드(dense node) → 성능 역전**. plane마다 트리 전체 복제(scope_to_branch fan-out)라 Branch가 수천 노드에 엣지.
  - 읽기: `(b:Branch)-[:ON_BRANCH]->(n) WHERE …`는 b의 관계를 전부 확장 후 필터 = **O(plane 크기)** → 인덱스 property seek보다 **더 느림**(진입점 빠르게 하려던 목적이 역전).
  - 쓰기: 매 노드 ingest마다 `MERGE (b)-[…]->(n)` 존재확인이 슈퍼노드 관계 스캔 → 브랜치 커질수록 적재 지연 + 병렬 시 핫 노드 **락 경합**. Neo4j dense-node 최적화가 완화하나 제거 못 함.
  - + id에 이미 branch → **identity 모델과 이중화**.
  - (대조: 기존 Repo→Package→File→Class 계층은 노드당 fan-out이 bounded라 슈퍼노드 아님. Branch→모든 노드는 평면 O(N)이라 다름.) → 나쁨.
- **경량 Branch 노드**(앵커 + 메타 + 계보만, **per-node 엣지 없이**; 코드는 인덱스된 property로 도달) = 폭발 없이 "branch를 질의 가능한 객체 + 계보". ADR-20260703(정체성은 property 유지) **보완**, CaptureManifest와 역할 정리 필요. → 현실적 선택지.

## 4. 판단 (방향)

- **성능/진입점 목적** → **속성 인덱스**로 충분(값싸고 즉효). Branch 노드 불필요.
- **branch 메타·계보(BRANCHED_FROM/MERGED_INTO)를 자주 1급으로 쓴다면** → **경량 Branch 노드** 값어치. reconcile이 branch 중요하게 쓰나, 현재 property+manifest로 이미 동작 → 당장 필요는 낮음.

## 5. 미해결 질문 (Open Questions)

- **E-Q1**: branch-진입 쿼리 실측 병목이 실제 있나? 있으면 우선 **속성 인덱스**로 해소(노드 reify 없이).
- **E-Q2**: 경량 Branch 노드를 도입하면 기존 `CaptureManifest`와 어떻게 정리? (Branch 노드가 흡수 vs 별개 유지)
- **E-Q3**: branch 계보(BRANCHED_FROM/MERGED_INTO)를 실제로 회상/질의에 쓸 유스케이스가 있나? (없으면 만들지 않는다, §4-3)
- **E-Q4**: Branch 노드가 정체성 차원(id의 branch)과 이중화되지 않게, "메타/계보 앵커"로만 한정하는 계약을 어떻게 강제?

# [F] 코드베이스 품질 이해·정규화 substrate — pre-compute 질의가능 팩트층

## 0. 동기 (사용자 제기 2026-07-12 · GitHub issue #1)

palimpsest(그리고 palimpsest를 소비하는 AI 에이전트)가 코드베이스를 **잘 이해**하려면 그 전제가 **코드베이스 품질 = 이해하기 쉬운 구조**다. 그래서 palimpsest에 역할이 하나 더 붙는다: 코드베이스의 특성을 이해하고 **정규화된 패턴을 제공**해 구조를 **지속적으로 가꿔나가게** 돕는 것.

이건 `incognito050924/ditto#9`("ACG 개선 — Meta의 pre-compute")의 **palimpsest측 진짜 동기**다. ditto#9가 Meta 기법 중 pre-compute(Glean식 정규화·질의가능 코드-팩트 색인)를 든 이유가 성능 최적화가 아니라 **정규화된 코드베이스 이해 substrate**라는 것. (이 관계는 이슈 #1 기준; ditto#9 원문은 외부 repo라 미검증.)

> 이슈 #1은 seed다: **아직 결정 아님.** 여기서도 동기·관계·열린 질문만 쌓고 조기 잠금하지 않는다. 범위(F-Q4/F-Q1/F-Q2)가 정해지면 그 부분만 ADR로 승격.

## 1. 사실 (코드·ADR 확인)

- **"질의가능 색인"의 현행 실현 = KG(Neo4j projection)**. git=SoT에서 결정론적으로 재구축되는 그래프가 이미 "재파싱 대신 질의"를 부분 실현(주제 C §1, `docs/research/precompute-hugrag-kg.md`). → Glean식 pre-compute의 *패턴*은 골격이 이미 있다.
- **그러나 정밀 substrate는 아직 미완**: 구조 팩트를 뽑는 `extract/java.py:250`이 여전히 **name-based over-matching**(`CALLS`가 단순명 같은 모든 메서드에 연결). 정밀 spine 개선(재검토 백로그 T9-PRIMARY)은 **미착수**.
- **환경 비종속은 기존 불변식으로 이미 박혀 있다**: build-less 전이력 균일(backfill = `git archive` 트리 파싱, ADR-20260702-backfill), recall+load LLM-free(provider-free narrowing, ADR-20260706 §결정3), git=SoT·drop→reload 결정론 재구축. → 새 substrate 역할도 이 불변식 안에서 성립해야 한다.
- **Glean *도구* 자체는 이미 기각**: ADR-20260706 §결정6 근거 — build-less+다언어+정밀 콜그래프를 동시 만족하는 성숙 도구는 없다(트릴레마). Glean·SCIP·Kythe·stack-graphs 전부 빌드 필요 또는 아카이브라 기각. → 채택하는 건 Glean이라는 **도구가 아니라 pre-computed-queryable이라는 패턴**이고, 그 패턴은 palimpsest가 **소유한 build-less tree-sitter spine + KG projection**으로 실현한다.

## 2. 관계 (다른 주제·이슈와의 얽힘)

- **substrate 후보 = 주제 C / 백로그 T9의 build-less tree-sitter 정밀 spine.** "정규화된 질의가능 팩트"의 생산 엔진이 곧 정밀 spine이다. F는 그 spine을 **무엇에 쓰는가**(코드 품질 이해)를 얹는 소비 관점 → C(생산)와 F(활용)는 상호 의존.
- **주제 D(test-impact)와 동형**: D도 "정밀 콜그래프 위에서 답하는 상위 질의". F의 "품질 신호"도 같은 spine 위 파생 → C가 D·F 공통 하부.
- **주제 A(온톨로지 일반화)가 선행**: 품질 신호가 다언어에서 의미 있으려면 FUNCTION 노드·de-Class화(A §5)가 먼저다. Class 가정이 남으면 함수-우선 코드의 품질을 못 잰다.
- **Curate(생성형) 축의 코드-품질 적용**: "정규화된 패턴 제공 + 가꾸기"가 단순 팩트 노출을 넘어 **안티패턴·리팩터 후보를 합성**하는 데까지 가면, 그건 VISION 핵심기능 Curate(생성형 합성, `docs/VISION.md:36`)의 코드-품질 특화다(이슈 표현 "정체성 축 B"). 그럼 생성형 큐레이터 아크(ADR-20260706, 백로그 T1–T4)의 격리-생산자·form-separation(세탁 금지) 규약을 그대로 상속해야 한다.
- **ditto#9는 소비자**: palimpsest가 이해·정규화 substrate를 소유하고 ditto ACG는 그걸 소비(환경 비종속·advisory 유지). 이슈 #1이 앵커로 지목한 **ADR-20260712("환경 비종속")는 아직 저장소에 없다**(로컬·origin/main 모두 부재). 원칙은 위 §1 기존 불변식으로 성립하나, 전용 ADR은 미착지 — **drift 주의**.

## 3. substrate 형태 — 후보 (제안, 미확정)

- **F-후보1 (유력): 기존 KG projection 위에 파생.** 품질 신호(메트릭·안티패턴 flag)를 정밀 spine이 채운 그래프 위 **결정론적 계산**으로 얹는다(주제 B의 coverage flag와 동형: LLM 불필요, detect-only). 별도 색인 인프라 없음 → git=SoT·재구축 결정론 무저촉.
- **F-후보2: 별도 Glean식 색인.** KG로 부족한 질의(대규모 팩트 조인·데이터플로우)가 실제로 생기면 전용 색인. 비용: 두 번째 SoT 위험(재구축 결정론·이중화) → 실측 병목 증거 전엔 안 만든다(§4-3).
- **표현 위상**: 품질 신호를 **1급 노드/엣지로 materialize** vs **회상 시 계산 채널로 lazy 산출** — 주제 D-Q3(임팩트를 채널 vs materialize)과 같은 축.

## 4. "가꾸기"의 경계 — seed 판단 (미결, 잠그지 않음)

- palimpsest는 **advisory substrate**다: 이해·품질 신호를 **관측 가능**하게 만들 뿐, 실제 코드 변경은 소비자(ditto ACG/개발자) 몫. 이는 stale 감지(주제 B §6: "self-healing 아님, 관측 가능하게만")·seam 대체(`docs/VISION.md:49`) 자세와 정합.
- **경계 위험**: "안티패턴·리팩터 후보"가 참/거짓 **판정(content-verdict)**으로 굳으면 ADR-20260706의 "content-verdict 외부 유지"(자기인증 회피)와 충돌. 품질 신호는 **근거결박된 관측**(출처+gap+confidence)으로 내보내되 판정을 내부에서 세탁하지 않아야 한다 → F-Q6.

## 5. 미해결 질문 (Open Questions) — 이슈 #1 F-Q 정리

> 순서: **앵커 위상(F-Q4)** → 산출물(F-Q1) → substrate 형태(F-Q3) → 가꾸기 경계(F-Q2) → 측정 기준(F-Q5) → 자기인증 경계(F-Q6, 신규). ★ = 이슈 "다음 단계"가 ADR 승격 게이트로 지목. (F-Q1~5 ID는 이슈 #1과 매핑 유지, 순서만 논리적으로 재배열.)

- **F-Q4 ★ (앵커 위상 — 나머지를 게이팅)**: 코드품질 이해가 **새 최상위 목적(정체성)**인가, 아니면 기존 **Relate/Curate의 코드-품질 특화 적용**인가? 정체성이면 5기능 개정, 특화면 기존 축 아래 슬라이스 — 이 판단이 F-Q1·F-Q2 답을 좁힌다.
- **F-Q1 ★ (산출물)**: "정규화된 패턴"의 구체 산출물은? 구조 메트릭 / 이해도 신호 / 안티패턴 flag / 리팩터 후보 중 무엇을, 어느 입도로? (실제 질의에 쓰이는 것만 만든다 — 안 쓰이면 안 만든다, §4-3.)
- **F-Q3 (substrate 형태)**: 기존 KG projection 위 파생(F-후보1)으로 충분한가 vs 별도 Glean식 색인(F-후보2)이 필요한가? git=SoT·drop→reload 결정론을 깨지 않는 선에서.
- **F-Q2 ★ (가꾸기 주체·경계)**: palimpsest는 이해·신호 substrate만 제공하고 실제 변경은 소비자(ditto ACG/개발자)인가? — advisory·환경 비종속과 정합하는 경계 확정.
- **F-Q5 (측정 기준)**: "코드베이스 품질 / 이해하기 쉬운 구조"의 **측정 가능한 정의**는? (응집도? 콜그래프 지역성? 명명 일관성?) — **소유권 계약**: F-Q5는 facet **열거**만 하고, 그중 **coverage/응집 facet의 SoT는 주제 B(#6)가 소유**한다. F는 이를 **재정의하지 않고 B에 위임**하며(중복정의=drift 방지, 주제 B §7), B는 F의 **첫 실증 파일럿**이다(B-후보1 ≡ F-후보1, 동형). #1과 #6은 이 계약으로 **분리 유지 + 교차링크**(합치지 않음).
- **F-Q6 (자기인증 경계 · 신규, 근거결박)**: 안티패턴·리팩터 후보 산출이 내부 **content-verdict**(참/거짓 판정)가 되지 않고 근거결박 관측(출처+gap+confidence)으로 남게 하는 규약은? (ADR-20260706 content-verdict-외부유지 · no-laundering 정합 — §4 경계 위험에서 파생.)

## 다음 단계

F-Q4/F-Q1/F-Q2(앵커·산출물·경계)가 정해지면 그 부분만 ADR로 승격. 그 전까지 주제 C(정밀 spine 생산)·D(test-impact)·A(온톨로지 일반화)·ditto#9와 함께 발전한다. 생산 엔진(C/T9)이 안 서면 F의 품질 신호도 기반이 없으므로 — **C/T9 착지가 F의 실질 선행**이다.

---

# 변경 이력 (changelog)

- 2026-07-05 — 최초 작성(주제 A: 다언어 온톨로지). 파일명 `multilang-ontology.md`→`design-notes.md`로 일반화, 주제 색인 도입. 주제 B(Summary 커버리지·응집) 추가.
- 2026-07-05 — 주제 B에 §6(신선도 축과의 구분) 추가 — faithfulness/freshness/coverage 세 축 비교 + stale 감지 사실 정리.
- 2026-07-05 — 주제 C(구조 추출 정밀도) 추가 — source-only AST(현재) vs CodeQL/외부 producer, 빌드 의존 충돌, AST spine + HEAD overlay 계층 분리, multilang 비용 재프레임(파서 작성만 줄고 온톨로지 일반화는 A §5로 잔존).
- 2026-07-05 — 주제 C에 §5 방향(direction, 사용자 선호) 추가 — 외부 정적도구 적극 도입 + CodeQL 이중 producer(구조+위험→Risk). 결정론적 CodeQL Risk vs inferred LLM Risk 분류 문제 + C-Q5/Q6 추가.
- 2026-07-05 — 주제 D(테스트 코드 모델링 & 테스트 임팩트) 추가 — 현재 테스트 미구분(확인), 3갭(구분/COVERS/간접 미인지), 마커+COVERS 엣지+임팩트 회상 제안, 주제 C(정밀 콜그래프)와 상호 강화. D-Q1~4.
- 2026-07-05 — 주제 E(브랜치 모델링) 추가 — branch property(현재, 인덱스 없음) vs Branch 노드 reify. 성능은 속성 인덱스로 해결, 풀 Branch 노드는 엣지 폭발+identity 이중화라 경량(메타·계보 앵커)만 후보. E-Q1~4.
- 2026-07-12 — 주제 F(코드베이스 품질 이해·정규화 substrate) 추가 — GitHub issue #1(seed) 물질화. 동기(품질=이해가능 구조 전제, ditto#9의 palimpsest측 동기), substrate=주제 C/T9 build-less tree-sitter spine(Glean *도구*는 트릴레마로 기각·*패턴*만 채택, ADR-20260706 §결정6), advisory 경계(변경은 소비자). F-Q1~5 정리(ID 유지·논리 재배열) + F-Q6(자기인증 경계) 신규. ADR-20260712("환경 비종속")는 이슈 참조하나 저장소 미착지(drift 기록).
- 2026-07-12 — drift 정리 — 주제 C §5(방향: 외부도구 적극도입 + CodeQL 이중 producer)와 색인 방향 라벨 삭제 — ADR-20260706 §결정6이 spine 주·CodeQL 보조로 역전했으므로. §6 미해결 질문 → §5 재번호. 정확한 방향은 ADR-20260706·ADR-20260712·재검토 백로그에 있음.
- 2026-07-13 — #1(F)·#6(B) 소유권 계약 반영 — 합치지 않고 분리 유지. F-Q5는 facet 열거만·coverage/응집 SoT는 주제 B가 소유(F는 재정의 없이 위임), B는 F의 첫 실증 파일럿(B-후보1 ≡ F-후보1 동형). 주제 B에 §7(주제 F와의 소유권 경계) 추가, F-Q5 문구 날카롭게. GitHub #1↔#6 상호 링크 코멘트.
