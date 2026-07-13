# ADR-20260713-sveltekit-routing-ontology — SvelteKit 라우팅 온톨로지: 프레임워크-특정 1급 노드/엣지 + §4-3 override + 콜로케이션(제3의 cross-language 다리, SC-B와 직교)

- 식별자: `ADR-20260713-sveltekit-routing-ontology` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-13
- work item: wi_260713ruv (issue #20 — 다언어 온톨로지: Svelte/SvelteKit 라우팅·프레임워크 시맨틱)

## 맥락

이슈 #9의 Svelte 추출기(`extract/svelte.py`)는 `<script lang=ts|js>` **블록 내부**의 함수·import·call만 2단 파싱으로 추출한다(byte-slice→TS/JS 재파싱). SvelteKit의 **파일기반 라우팅·프레임워크 규약**은 모델하지 않는다. 이슈 #9는 이 라우팅·프레임워크 계층을 범위 밖으로 명시 유예했고(issue #20으로 분리), 착수 전 **설계 선결**로 다음을 정해야 했다.

1. **일반 온톨로지 vs. 프레임워크-특정.** 어디까지가 일반 FUNCTION/CALLS/IMPORTS로 충분하고, 어디부터 SvelteKit-전용 1급 노드·엣지가 필요한가. 프레임워크 특화는 온톨로지 일반성과 트레이드오프이며, 프로젝트 헌장 §4-3("가장 단순한 해법 우선")이 경계하는 대상이다.

2. **목표 질의가 요구하는 구조.** 이 계층의 목표 설계-위험 질의는 "가드 없이 민감 코드에 도달하는 엔드포인트"와 URL↔파일 매핑이다. palimpsest는 판단하지 않으므로(inferred 날조 금지), 질의가 위험한 형태를 **스스로 드러낼 만큼** 구조를 모델해야 한다.

3. **cross-language 결선(結線)의 경계.** 라우트 콜로케이션(colocation)은 `.svelte`와 `.ts/.js`를 한 라우트로 묶는다. 이는 언어 패밀리 경계를 넘는 결선이므로, `ADR-20260713-multilang-depends-on-name-local-boundary`의 SC-B(name-based 해석의 언어-로컬 경계)와 충돌하지 않는지 확인해야 한다.

## 결정

### 결정 1 — SvelteKit-전용 라우팅 온톨로지 (의도적 프레임워크-특정)

SvelteKit 파일기반 라우팅을 **4개 1급 노드 kind + 4개 엣지 kind + File 속성 하나**로 1급화한다.

- **노드 kind (4):** `Route` · `Endpoint` · `Layout` · `Hook`.
- **엣지 kind (4):**
  - `REALIZES`: File → routing node (라우트 파일이 라우팅 노드를 실현).
  - `HANDLES`: Function → Endpoint (`+server.ts`의 HTTP 핸들러 함수가 엔드포인트를 처리).
  - `LOADS`: load Function → Route (`+page.(server.)ts`의 `load`가 라우트에 데이터를 공급).
  - `GUARDS`: Hook | Layout → Route | Endpoint (가드 상속, **keystone-scoped**).
- **File 속성:** `server_only` (`.server` 파일에 부착되는 서버/클라이언트 경계 마커).
- **모델 범위:** ① 구조(파일기반 라우팅) · ② 데이터흐름(`load` via `LOADS`, `+server` 핸들러 via `HANDLES`) · ③ 서버/클라 경계(`server_only`) · ④ 가드 상속(`GUARDS`, keystone-scoped) · ⑤ endpoint→handler→`CALLS` 도달성("엔드포인트가 도달하는 민감 함수").
- **Route 정체성 = 정규화 URL.** `(group)`은 URL에서 제거하고, `[slug]`·`[...rest]`·`[[opt]]`는 정규화한다. 단 **`[name=matcher]`는 matcher를 유지**한다 — matcher를 지우면 서로 다른 형제 라우트가 같은 URL로 접혀 충돌하기 때문이다. 재적재는 멱등(같은 입력 두 번 → 같은 그래프).
- **Endpoint는 HTTP 메서드별**로 승격한다(`GET`/`POST`/… 각각 하나의 Endpoint).
- 각 routing 노드는 **대표 파일**(`+page.svelte`/`+server.ts` 등)에 결박한다.
- 이 온톨로지는 **의도적으로 프레임워크-특정**이다. React/Vue 등 다른 프레임워크로의 framework-neutral 일반화는 **유예**한다(투기적 일반화 금지).

### 결정 2 — §4-3(가장 단순한 해법 우선) override 정당화

헌장 §4-3은 프레임워크-특정 기계와 단일사용 추상을 경계한다. 결정 1은 그 기본값을 **명시적으로 override**하며, 정당화는 다음과 같다.

- 목표 설계-위험 질의 "가드 없이 민감 코드에 도달하는 엔드포인트"는 일반 FUNCTION/CALLS/IMPORTS 온톨로지로 **답할 수 없다.** `+server.ts`는 일반 온톨로지에서 그저 FUNCTION들을 담은 File일 뿐이고, 무엇도 그것을 "가드 없는 HTTP 진입점"으로 표시하지 않으며, layout/hooks의 가드 상속 keystone은 일반 표현이 없다.
- palimpsest는 판단하지 않는다. 위험을 대신 결론짓지 않으므로, 질의가 위험한 형태를 스스로 드러내려면 구조를 그만큼 충분히 모델해야 한다 — 여기서 프레임워크-특정 노드/엣지는 표현 능력의 하한이다.
- 추가된 kind는 **수용된 acceptance criteria(ac-1~ac-6)가 요구하는 최소 구조**이지 투기적 확장성이 아니다. 각 kind는 하나 이상의 AC 질의에 결박되며, AC가 요구하지 않는 형태(form actions, 마크업 레벨 등)는 범위 밖으로 유예했다(§정직성·범위 밖).

### 결정 3 — 콜로케이션(colocation) = 제3의 cross-language 다리, SC-B와 직교

`ADR-20260713-multilang-depends-on-name-local-boundary` §결정2(SC-B)는 name-based `CALLS`/`DEPENDS_ON`을 **언어-패밀리-로컬**로 고정하고, 유일한 cross-family 다리를 **명시적 `IMPORTS` specifier 해석**으로 둔다. 라우팅 콜로케이션은 이와 **직교하는 제3의 다리**다.

- Route는 같은 라우트 디렉터리의 `.svelte`(`+page.svelte`)와 `.ts/.js`(`+page.server.ts`)를 **결정론적 파일시스템 인접(same-dir)**으로 묶는다. 이는 **이름 매칭이 아니다.**
- 식별자를 패밀리 경계 너머로 매칭하지 않으므로, SC-B가 방지하는 동명 false-positive(`.ts`의 `x: Foo` ↔ `.js`의 `class Foo`)를 **재도입하지 않는다.** 콜로케이션은 이름이 아니라 디렉터리 위치로만 결선한다.
- 결과적으로 이제 서로 **직교하는 3개 cross-language 다리**가 존재한다:
  1. **SC-B**: 패밀리-내(family-local) name 해석 (`CALLS`/`DEPENDS_ON`).
  2. **IMPORTS**: 상대경로 specifier 해석 (드라이버 레벨 `_resolve_imports`).
  3. **NEW — 라우팅 콜로케이션**: 라우트-디렉터리 인접(same-dir) 결선 (`REALIZES`/`LOADS` 등).

## 정직성·범위 밖 (honesty / out-of-scope)

- **GUARDS/server_only 보안-시맨틱은 lower-bound다.** 정적 경로기반 탐지는 실제 가드(hooks.server `handle` 로직, `load`의 redirect, 세션/`locals` 체크)를 **놓치거나 과다부여**할 수 있다. 따라서 회상 질의는 누락된 `GUARDS`를 확정적 "미보호/public"로 제시하지 않고 **sound 공백을 명시**한다 — `recall/graphrag.py`의 static-lower-bound 패턴(`_STATIC_LOWER_BOUND_GAP`)과 동형이다: 빈/짧은 결과를 "가드 없음"으로 읽어선 안 되며 완전성을 주장하지 않는다.
- **v1 범위 밖(명시 유예):** form actions, layout `load`를 `LOADS` 엣지로 표현하는 것, 마크업 레벨(script 블록 밖 템플릿 표현식·컴포넌트 합성·store 구독), 설정 가능한 라우트 디렉터리.
- **공개된 정적 탐지 공백:** 래핑 핸들러(`export const GET = protect(...)`)와 endpoint self-check(`locals` 기반 자체 검사)는 정적 경로 탐지가 놓치는 공백으로, 위 lower-bound 원칙에 따라 공백으로 드러낸다.

## 근거 (rationale)

- 목표 질의가 요구하는 최소 구조에서 역산했다: "가드 없이 민감 코드에 도달하는 엔드포인트"가 참이려면 (a) 엔드포인트가 1급이어야 하고(`Endpoint`+`HANDLES`), (b) 가드가 상속 가능한 keystone이어야 하며(`GUARDS`, layout/hooks), (c) 민감 함수 도달성이 추적 가능해야 한다(endpoint→handler→`CALLS`). 셋 다 일반 온톨로지엔 표현이 없다.
- `[name=matcher]`의 matcher 보존은 정체성 충돌 회피의 최소 조치다: matcher는 서로 다른 URL 공간을 가르는 판별자이므로, 지우면 형제 라우트가 한 노드로 접혀 멱등(ac-3)과 URL↔파일 매핑이 깨진다.
- 콜로케이션을 디렉터리 인접으로 둔 것은 SC-B의 전제를 지키기 위함이다: 이름을 패밀리 경계 너머로 매칭하는 순간 동명 오탐과 community 오병합이 재발한다. 위치 기반 결선은 그 위험을 원천 차단한다.
- 프레임워크-특정을 감수한 것은 §4-3의 예외이지 폐기가 아니다 — 두 번째 파일-라우팅 프레임워크가 등장하기 전에는 framework-neutral 일반화가 단일 사용 추상(§4-3이 금지)이 되므로, 일반화를 **유예**하는 것이 오히려 §4-3에 부합한다.

## 관계

- `ADR-20260713-multilang-depends-on-name-local-boundary` §결정2(SC-B)와 **직교**한다(결정 3): 라우팅 콜로케이션은 name 해석이 아니라 디렉터리 인접이므로 SC-B의 언어-로컬 경계를 침범하지 않는 제3의 cross-language 다리다.
- `ADR-20260706-generative-curator-direction` §결정6(언어별 `queries/<lang>/*.scm`가 build-less tree-sitter spine 소유, 리졸버는 언어-중립)의 연장선에 있으나, 라우팅은 grammar 노드가 아니라 **파일명 규약**에서 파생되므로 query가 아닌 파일-경로 인식 계층에서 승격한다.
- 헌장 §4-3(가장 단순한 해법 우선)을 명시적으로 override하고 그 정당화를 기록한다(결정 2) — §4-10(기록된 결정과 충돌하면 드러낸다)에 따라 조용히 넘기지 않는다.

## 철회·변경 조건 (change_condition)

- **결정 1(프레임워크-특정):** **두 번째 파일-라우팅 프레임워크**(예: Next.js app-router, Nuxt)를 도입하면 framework-neutral 일반화를 재검토한다 — 그 시점에야 공통 구조가 두 번 이상 사용되어 추상이 정당화된다. 그 전까지 React/Vue 확장은 유예한다.
- **결정 3(콜로케이션):** name-based가 아니라 디렉터리-인접 결선인 한 유지한다. 만약 라우트 결선을 이름 기반으로 바꾸려는 요구가 생기면 SC-B의 동명 오탐 위험을 다시 검토해야 한다.
- **정직성(lower-bound):** 정적 경로기반 탐지가 정밀(타입/데이터흐름 해석 기반) 가드 탐지로 바뀌면, `GUARDS`를 lower-bound로 제시하는 전제를 재검토한다.

## 근거·권위 (설계 선결 — 구현 선행)

이 ADR은 **구현 착수 전 설계 결정**이다(§4-11에 따라 코드가 없는 상태를 감춘 채 코드 SoT를 가리키지 않는다). 권위는 수용된 acceptance criteria와, 구현 시 이를 실현할 코드에 있다.

- 수용 기준: `.ditto/work-items/wi_260713ruv/record.json` `acceptance_criteria` ac-1~ac-9 (issue #20).
  - ac-1: +-접두 라우트 파일만 승격, co-located 컴포넌트·`src/lib`는 라우트 노드 아님.
  - ac-2: `+server.ts` HTTP 핸들러 → `Endpoint`(`HANDLES`), `+page.server.ts` `load` → 데이터흐름(`LOADS`).
  - ac-3: Route 정체성=정규화 URL, `(group)` 제거, `[slug]`/`[...rest]`/`[[opt]]` 정규화, 재적재 멱등.
  - ac-4: layout/hooks.server 가드 상속으로 "가드 없는 엔드포인트" 질의가 상위 가드 보호 엔드포인트를 오탐하지 않음.
  - ac-5: endpoint→handler→`CALLS` 도달성으로 "엔드포인트가 도달하는 민감 함수" 정답 반환.
  - ac-6: `server_only` 마커가 `.server` 파일에 부착, universal→server-only `IMPORTS` 경로 질의 가능.
  - ac-7~ac-8: 기존 community 파티션·비-라우팅 recall 불변(회귀), 새 kind가 `NODE_LABELS`/`REL_TYPES` 등록으로 fail-closed 적재 통과.
- 정직성 패턴의 코드 근거(동형): `src/palimpsest/recall/graphrag.py:1142-1150` `_STATIC_LOWER_BOUND_GAP` — 정적 탐지를 lower-bound로 명시하고 완전성을 주장하지 않는 기존 패턴.
