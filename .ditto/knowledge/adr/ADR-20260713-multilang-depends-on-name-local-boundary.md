# ADR-20260713-multilang-depends-on-name-local-boundary — 다언어 추출: DEPENDS_ON은 정적 타입 언어에만 + name-based 해석의 언어-로컬 경계(SC-B)

- 식별자: `ADR-20260713-multilang-depends-on-name-local-boundary` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-13
- work item: wi_260713lom (issue #9 — TS/JS/React/Svelte extractor)

## 맥락

이슈 #9는 ECMAScript 계열(TS/TSX/JS/JSX + Svelte) 정적 추출기를 도입한다. 두 가지가 정초의 원래 방향과 어긋났다.

1. **DEPENDS_ON 방향(A-Q4).** 설계노트 [A]의 원래 A-Q4 방향은 "동적 타입 언어 도입 시 DEPENDS_ON을 약화"였고, 이슈 #9는 TS를 순수 JS와 함께 "동적 타입"으로 묶었다. 그러나 TypeScript는 순수 JS와 달리 실제 정적 타입주석(필드·파라미터)을 가진다. DEPENDS_ON은 java.py 방식대로 필드·파라미터 **타입주석의 참조 타입명**에서 파생되므로, TS에서는 약화할 대상이 아니라 **추가할 근거**가 있다.

2. **name-based 해석의 언어 경계.** CALLS·DEPENDS_ON은 이름 기반(unqualified simple name)으로 해석된다. 다언어를 한 IR로 통합할 때 union-wide로 재해석하면, `.ts`의 `x: Foo` 타입주석이 `.js`의 `class Foo`에 매칭되는 동명 오탐이 생기고, community 그룹핑이 기존 Java/Kotlin community까지 오병합할 수 있다.

## 결정

### 결정 1 — DEPENDS_ON 비대칭 (A-Q4 정련)

DEPENDS_ON은 **정적 타입주석을 가진 언어에만** 방출한다.

- `.ts/.tsx`(및 svelte `<script lang=ts>`)에만 Class/Module→Class DEPENDS_ON을 방출하고, `.js/.jsx`·svelte-js-script에는 방출하지 않는다.
- 이슈 #9가 TS를 JS와 함께 "동적 타입"으로 묶은 전제를 교정한다: TS는 정적 타입주석을 가지므로 DEPENDS_ON을 약화가 아니라 **추가**한다.
- 이 비대칭은 런타임 분기가 아니라 **query-compile 경계**로 구조적으로 강제된다. `queries/typescript/types.scm`은 TS grammar에만 있는 노드 타입(`public_field_definition`·`required_parameter`·`type_annotation`)을 참조해 typescript/tsx grammar에서만 컴파일되고 javascript grammar에서는 `QueryError`를 낸다 — JS 빌드는 타입 쿼리를 로드조차 할 수 없다(`LangProfile.collect_types` 플래그는 walker 측 보강일 뿐, 경계 근거는 컴파일).

### 결정 2 — SC-B: name-based 해석의 언어-로컬 경계

다언어 통합 추출에서 이름 기반 **CALLS와 DEPENDS_ON은 각 언어 fragment(ts=.ts+.tsx, js=.js+.jsx, svelte)의 자기 노드 집합 안에서만** 해석한다.

- union-wide 재해석을 하지 않는다. `_calls_edges`·`_depends_on_edges`는 각 fragment의 nodes만 입력으로 받으므로 이름 매칭이 언어 경계를 넘지 못한다.
- cross-language 연결은 **명시적 IMPORTS specifier 해석(드라이버 레벨 `_resolve_imports`)만**으로 이룬다. 상대 specifier를 대상 File의 qualified_name으로 재기록해 그래프를 하나로 잇되(ac-2), 이름 기반 재해석은 하지 않는다.

## 근거 (rationale)

- 이름 기반 해석이 언어 경계를 넘으면 동명 오탐이 생긴다: `.ts`의 `x: Foo`(파라미터 타입주석)가 `.js`의 `class Foo`에 매칭될 수 있다.
- community 그룹핑(`kg/community.py:96-111` `_unit_level_pairs`)은 **CALLS + DEPENDS_ON**을 소비하고 **IMPORTS는 소비하지 않는다**. 따라서 오탐 CALLS/DEPENDS_ON은 곧바로 기존 Java/Kotlin community를 오병합한다. IMPORTS만 언어 경계를 넘게 하면 그래프는 하나로 연결되되(ac-2) 오병합은 없다 — 경계선이 정확히 "community가 소비하는 엣지 = 언어-로컬, 소비하지 않는 엣지(IMPORTS) = cross-language"에 놓인다.
- DEPENDS_ON을 정적 타입 언어에만 두는 것은 "지어내지 않는다" 원칙과 일치한다: JS는 타입주석이 없으므로 DEPENDS_ON을 inferred로 날조하지 않고 아예 방출하지 않는다.

## 관계

- 설계노트 [A]의 A-Q4("동적 타입 언어 도입 시 DEPENDS_ON 약화")를 **정련**한다 — TS는 정적 타입주석을 가지므로 약화 대상이 아니라 추가 대상.
- `ADR-20260706-generative-curator-direction` §결정6(언어별 `queries/<lang>/*.scm`가 build-less tree-sitter spine을 소유, 리졸버는 언어-중립)을 따른다. 공유 `queries/ecmascript/tags.scm`은 세 grammar 전부에서 컴파일되고, `queries/typescript/types.scm`은 TS grammar에서만 컴파일된다.
- de-Class 하부(FUNCTION 노드 + community `_unit_of`의 Class∪Module 일반화, commit 138db70)를 재사용한다 — top-level Function 파라미터 타입주석은 그 함수의 File/Module을 container로 DEPENDS_ON을 방출한다.

## 철회·변경 조건 (change_condition)

- **결정 1**: 어떤 언어가 정적 타입주석을 갖지 않으면 그 언어엔 DEPENDS_ON을 두지 않는다(inferred로 지어내지 않는다). 반대로 새 언어가 정적 타입을 가지면 같은 방식으로 추가한다.
- **결정 2(SC-B)**: 이름 기반 해석을 쓰는 한 유지한다. 만약 정밀(타입 해석 기반) 해석기로 바뀌면(예: issue #2 receiver-typed CALLS) 언어-로컬 경계의 근거를 재검토한다 — 타입 해석은 심볼을 정확히 지목하므로 동명 오탐 위험이 사라져 경계 규칙의 전제가 달라진다.

## 근거·권위 (코드 — SoT)

commit 4eaf85b (origin/main):

- `src/palimpsest/extract/ecmascript.py` — `_EcmaWalker`(구조 노드 + CONTAINS/IMPORTS), `_calls_edges`/`_depends_on_edges`(per-fragment 이름 기반, docstring이 SC-B 명시: "Because nodes is a single fragment's nodes, a call never resolves across the language-family boundary"), `_resolve_imports`(union-wide specifier 해석), `finalize_ir`, `LangProfile.collect_types` 게이트, `_EXCLUDED_DIRS`(vendored 제외).
- `src/palimpsest/extract/queries/typescript/types.scm` — TS-only 컴파일 경계(주석 4-9행이 QueryError 근거 명시).
- `src/palimpsest/extract/typescript.py`/`javascript.py`/`svelte.py` — per-family 어댑터(svelte는 `<script lang>` 2단 파싱 + line_offset).
- `src/palimpsest/extract/__init__.py` — `extract_ecmascript` 드라이버(ts→js→svelte fragment 격리 후 union-wide finalize).
- 불변 확인: `src/palimpsest/kg/community.py:96-111` `_unit_level_pairs`가 DEPENDS_ON·CALLS만 소비하고 IMPORTS는 소비하지 않음.
