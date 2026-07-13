# ADR-20260713-endpoint-framework-neutral-generalization — Endpoint 온톨로지 framework-neutral 일반화(공유 label + tier 판별자) + cross-tier CALLS_API 다리 — ADR-20260713-sveltekit-routing-ontology supersede

- 식별자: `ADR-20260713-endpoint-framework-neutral-generalization` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-13
- work item: wi_260713c7t (다언어 온톨로지 — Spring Framework 시맨틱 + f/e↔b/e cross-tier API 링크)
- supersedes: `ADR-20260713-sveltekit-routing-ontology`

## 맥락

`ADR-20260713-sveltekit-routing-ontology` 결정 1은 `Route`/`Endpoint`/`Layout`/`Hook` 라우팅 온톨로지를 **의도적으로 프레임워크-특정**으로 두고, framework-neutral 일반화를 명시적으로 **유예**했다. 그 ADR의 change_condition은 이렇게 적혀 있었다:

> **두 번째 파일-라우팅/endpoint-producing 프레임워크**를 도입하면 framework-neutral 일반화를 재검토한다 — 그 시점에야 공통 구조가 두 번 이상 사용되어 추상이 정당화된다.

**Spring이 바로 그 두 번째 프레임워크다.** wi_260713c7t가 Spring(Kotlin+Java) 엔드포인트 추출을 도입하며 그 change_condition이 충족됐다. 이제 같은 HTTP route가 두 tier에서 선언된다: SvelteKit f/e `@GetMapping` 대응 `+server.ts` 핸들러와 Spring b/e `@GetMapping("/api/orders")`.

여기서 두 요구가 정면충돌한다:

1. **균일 회상.** `MATCH (ep:Endpoint)` 질의는 tier에 무관하게 모든 엔드포인트를 균일하게 잡아야 한다(라벨 분화는 질의를 tier마다 갈라놓는다).
2. **병합 금지.** f/e와 b/e의 **동일 경로** 엔드포인트는 서로 다른 실체다 — 하나로 MERGE되면 안 된다(ac-7).

그리고 이 스택의 최고가치 시맨틱은 Spring 단독이 아니라 **간접연결의 표면화**다: Svelte `fetch('/api/orders')` ↔ Spring `@GetMapping("/api/orders")`는 어느 파일 코드에도 직접 `CALLS`로 나타나지 않는다 — 이 연결을 그래프에 얹는 것이 palimpsest의 본질 가치다.

## 결정

### 결정 1 — 공유 label + tier 판별자 (ADDITIVE, SvelteKit 불변)

Endpoint는 모든 tier에서 **공유 `label=Endpoint`**를 유지하되(`MATCH (ep:Endpoint)` 균일), qualified_name에 **tier/framework 판별자**를 얻는다.

- **SvelteKit f/e (grandfathered, 접두 없음):** qualified_name은 정확히 `f"{func.name} {url}"`(예: `"GET /api/orders"`) 그대로 — byte 불변. `_normalize_url` 미변경, #20 테스트 GREEN 유지(Frozen Invariant 1). 접두 부재 = SvelteKit plane.
- **Spring b/e (`spring:` 접두):** `endpoint_qualified_name(method, path)` → `f"spring:{METHOD} {normalize_endpoint_path(path)}"`(예: `"spring:GET /api/orders"`). `spring:` = `layout:`/`hook:` 같은 namespace 컨벤션.
  - `src/palimpsest/extract/spring.py:97-104` `endpoint_qualified_name`; `spring.py:166-194` `spring_endpoints`(class×method 매핑의 method×path 카티션); role/emission 게이트는 grammar-agnostic 하게 `spring.py`에 집중(java.py + kotlin.py 공유, 분기 없음).
- **병합 방지:** MERGE는 (label, id) 단위이므로 `GET /api/orders`(SvelteKit)와 `spring:GET /api/orders`(Spring)는 **DISTINCT 노드**다(ac-7). 판별자는 이 슬라이스에서 tier 토큰 상수 `spring`(서비스 단위 아님).
- **정체성 vs 매칭 두 층 분리 (코드 realize):** 정체성 정규화는 param 이름을 **보존**하고(`normalize_endpoint_path`, `src/palimpsest/ir.py:70` — 이름을 지우면 형제 route가 접힌다), 매칭 키만 param을 `{}`/`{**}`로 **소거**한다(`canonical_route_path`/`canonical_match_key`, `ir.py:115,130`). 절대 canonical 경로로 id를 만들지 않는다(Frozen Invariant 2).
- **tier-scoped 회상 (ac-7 회귀 0):** `src/palimpsest/recall/routing.py:47` `_SPRING_NS_PREFIX`, `routing.py:71-79` — SvelteKit "가드 없는 엔드포인트" 채널은 `qualified_name NOT STARTS WITH 'spring:'`로 b/e를 배제한다. Spring 엔드포인트는 label을 공유하지만 SvelteKit `GUARDS` 생산자가 없으므로, 배제하지 않으면 f/e 결과를 오염시킨다.

### 결정 2 — CALLS_API = 새 inferred cross-tier 다리 (route-string 매칭, SC-B와 직교)

f/e `ApiCall` ↔ b/e `Endpoint`를 **정규화 HTTP method+경로 문자열로 매칭**하는 새 inferred 엣지 `CALLS_API`를 도입한다.

- **route-string 매칭 = SC-B와 직교.** 이 매칭은 이름 해석이 아니라 정규화된 route 문자열 비교다. 따라서 `ADR-20260713-multilang-depends-on-name-local-boundary` §결정2(SC-B: name-based `CALLS`/`DEPENDS_ON`의 언어-로컬 경계)와 **직교**한다 — 식별자를 언어 경계 너머로 재해석하지 않으므로 SC-B가 막는 동명 오탐을 재도입하지 않는다. 이제 서로 직교하는 cross-tier/cross-language 다리가 넷이다:
  1. **SC-B**: family-local name 해석(`CALLS`/`DEPENDS_ON`).
  2. **IMPORTS**: 상대경로 specifier 해석.
  3. **라우팅 colocation**: same-dir 인접(ADR-20260713-sveltekit 결정 3).
  4. **NEW — CALLS_API**: 정규화 route-string 매칭.
- **inferred + 근거 + confidence (no-laundering).** CALLS_API는 `edge_kind='inferred'`로만 방출하고 grounding(matched_route)과 confidence 계급을 붙인다 — content-verdict를 결정론 층으로 세탁하지 않는다(`ADR-20260706`, Frozen Invariant 3). 구조적 강제:
  - `src/palimpsest/ir.py:261` `CALLS_API` 상수는 **의도적으로 `REL_TYPES`에서 배제**된다(SUMMARIZES/RISKS/DECIDES 선례). generic deterministic writer가 절대 이 엣지를 stamp할 수 없다.
  - `src/palimpsest/kg/ingest.py:304-307` fail-closed guard: `ir.edges`에 우발적 CALLS_API가 있으면 `edge kind ... is not a registered REL_TYPES member`로 거부 → 전용 loader 사용을 강제한다.
  - 전용 inferred loader가 유일한 생산자다(design contract Decision 4): 모든 ApiCall × 매칭 Endpoint를 Python에서 `canonical_match_key`로 매칭해 `MERGE ... SET r.edge_kind='inferred', r.confidence, r.matched_route, r.candidate_count`. confidence 계급: 1.0(정확 method+literal path 단일 후보) / 0.7(경로변수 존재) / 0.4(wildcard method). *(이 슬라이스 시점에 엣지 타입·no-laundering 분리는 ir.py+ingest guard로 이미 realize; 전용 loader는 design contract Decision 4로 계약된 유일 생산자다.)*
- **ApiCall 노드 = 결정론 구조.** `src/palimpsest/ir.py:205` `API_CALL="ApiCall"`는 `NODE_LABELS`에 등록되어 fail-closed 적재를 통과한다(`kg/ingest.py:71`, ac-8). 식별자는 `api_call_qualified_name(method, raw_url)`(`ir.py:149-174`)로 `apicall:{method} {static-template}`.
- **매칭 불가 = 정직한 gap (ac-6).** 동적/조립 URL(bare 변수·literal 없는 runtime concat)은 `api_call_qualified_name`이 `None`을 반환해 ApiCall을 아예 방출하지 않는다(`ir.py:149-174`). 링크 부재를 "엔드포인트 미사용"으로 단정하지 않는다 — static lower-bound, 완전성 미주장.

## supersede / 관계

- **`ADR-20260713-sveltekit-routing-ontology`를 supersede한다.** 그 ADR 결정 1의 "의도적 프레임워크-특정 + 일반화 유예"를 이 ADR이 "공유 label + tier 판별자"로 일반화하며 그 change_condition을 소진한다. **단, 폐기가 아니라 계승이다** — 그 ADR의 프레임워크-특정 노드/엣지(`Route`/`Endpoint`/`Layout`/`Hook`, `REALIZES`/`HANDLES`/`LOADS`/`GUARDS`)와 lower-bound 정직성은 그대로 유지된다: SvelteKit plane은 byte 불변이고, `GUARDS` lower-bound는 `routing.py` tier-scoping으로 계속 살아 있다. 원 ADR은 history 보존을 위해 삭제하지 않고 `상태: superseded`로 표시한다.
- **`ADR-20260713-ontology-framework-constructs-not-project-patterns`(판별 원칙)의 지배를 받는다.** Endpoint/ApiCall/CALLS_API는 프레임워크/표준 수준 구성물이므로 1급으로 넣고, 프로젝트 자체 래퍼는 배제한다(origin-keyed recognizer). 즉 이 ADR의 "무엇을 노드로 넣는가"는 그 원칙의 적용이다.
- **`ADR-20260706-generative-curator-direction`(no-laundering)** 무저촉: CALLS_API는 inferred + 근거 + confidence로 사실과 분리한다.
- **`ADR-20260713-multilang-depends-on-name-local-boundary`(SC-B)와 직교**(결정 2): route-string 매칭은 name 해석이 아니므로 언어-로컬 경계를 침범하지 않는 제4의 cross-tier 다리다.

## 정직성·범위 밖 (honesty / out-of-scope)

- **static lower-bound:** 동적/조립 URL, 프로젝트 래퍼 경유 호출은 gap으로 드러낸다(판별 원칙 ADR + ac-6). cross-service(S2S) WebClient 호출·config base-url 해석, 외부(third-party) API 호출은 follow-on WI 체인으로 완결한다(유예 아님).
- **Spring 보안은 정적 추출 밖:** `@PreAuthorize`/servlet filter chain 등 b/e 가드 mechanism은 정적 추출이 못 본다. 그래서 `routing.py`는 `spring:` plane을 SvelteKit "가드 없는 엔드포인트" 채널에서 **배제**한다 — Spring 엔드포인트를 SvelteKit-shaped(잘못된 mechanism) "unguarded"로 오탐하지 않기 위함이다. 이 ADR은 Spring 보안을 모델하지 않는다.

## 철회·변경 조건 (change_condition)

- **세 번째 endpoint-producing 프레임워크**가 와도 이 일반화(공유 label + 판별자)는 새 판별자 접두를 추가하는 것으로 충분하다 — 재론이 아니라 확장이다. 판별자를 tier 단위가 아니라 **service 단위**로 세분해야 할 때(S2S multi-backend에서 같은 route가 여러 서비스에 존재)만 판별자 축을 재검토한다(follow-on).
- **route-string 매칭이 데이터플로우 기반 정밀 매칭으로 대체**되면(래퍼 url 인자 추적, 동적 URL 복원) CALLS_API confidence 계급과 static-lower-bound gap의 전제를 재검토한다.
- **`spring:` 판별자 토큰 자체**는 tier 상수로 고정한다 — 서비스 판별이 필요해지기 전에는 바꾸지 않는다(과잉 일반화 금지).
