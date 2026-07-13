# ADR-20260713-ontology-framework-constructs-not-project-patterns — 온톨로지 모델링 경계: 프레임워크/언어/표준 구성물만 1급, 프로젝트 자체 코드 패턴은 배제(정직한 gap) — ADR-20260712 host-neutral 정체성의 연장

- 식별자: `ADR-20260713-ontology-framework-constructs-not-project-patterns` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-13
- work item: wi_260713c7t (다언어 온톨로지 — Spring Framework 시맨틱 + f/e↔b/e cross-tier API 링크; issue #20 SvelteKit의 b/e 대칭짝)

## 맥락

다언어 온톨로지가 HTTP API 시맨틱(엔드포인트 선언 + 호출부 + cross-tier 링크)으로 확장되면서, 전에 없던 근본 질문이 착수 선결로 떠올랐다: **온톨로지에 무엇을 1급 구성물로 넣고 무엇을 넣지 않는가.**

- 프로젝트마다 자기 HTTP 접근을 감싼 **자체 래퍼**(사내 `api.get()` 클라이언트, 사내 컨벤션, bespoke fetch wrapper)를 둔다. 온톨로지 추출기가 이런 래퍼 호출을 "API 호출"로 인식하려 들면, 그건 그 프로젝트 하나에만 참인 **우발적 패턴을 온톨로지에 새기는 것**이다 — false-positive를 남발하고, 다른 코드베이스로 옮기면 곧바로 오작동한다.
- `ADR-20260712-palimpsest-identity-host-neutral-generative-curator`는 palimpsest가 어떤 소비 환경에도 종속되지 않는다고 못박았다(host-neutral). 결정 1(b)·(c)는 소비자의 도메인 개념을 코어 온톨로지에 **내부화하지 않는다**고 했다. 프로젝트 자체 코드 패턴을 온톨로지에 내부화하는 것은 이 정체성의 **모델링 차원 위반**이다 — 소비자 결합과 같은 종류의 결합이다.
- 따라서 host-neutral 정체성을 온톨로지 모델링 경계로 실현하려면, "무엇이 프레임워크/표준이고 무엇이 이 프로젝트의 발명인가"를 가르는 **판별 기준**이 필요하다.

## 결정

### 결정 1 — 판별 질문 (discriminator)

온톨로지는 **프레임워크 / 언어 / 표준 / 널리 쓰이는 라이브러리 수준의 구성물만** 1급으로 모델한다.

판별 질문은 하나다:

> **이 구성물이 서로 독립적인 여러 코드베이스에 동일하게 나타나는가 — 프레임워크·표준·널리 쓰이는 라이브러리 — 아니면 이 프로젝트가 스스로 발명한 것인가?**

- **전자만** 온톨로지에 넣는다: HTTP 표준 `fetch`(전역), `axios`/`node-fetch`(패키지), Spring `@RestController`/`@GetMapping`(프레임워크), `RestTemplate`/`WebClient`/Feign(라이브러리) 등.
- **후자는 배제한다**: 프로젝트 자체 HTTP 래퍼 클래스/함수, 사내 컨벤션은 온톨로지에 넣지 않고 **정직한 gap**으로 둔다. 매칭되지 않는 호출을 "엔드포인트 미사용"으로 단정하지 않고, 완전성을 주장하지 않는다(static lower-bound).

이 경계는 자의적 취향이 아니라 위 판별 질문의 기계적 적용이다: 프레임워크/표준은 정의상 여러 코드베이스에 동형으로 재현되므로 온톨로지가 담을 보편 구조이고, 프로젝트 자체 패턴은 그 프로젝트 밖에서 참이 아니므로 담으면 곧 host-neutral 위반이다.

### 결정 2 — 원칙은 코드로 realize된다 (권위는 코드에, 헌장 §4-11)

이 판별 원칙을 **ADR(왜)에만 두지 않는다.** 헌장 §4-11(권위는 코드에 있다)에 따라, 원칙을 문서로 선언하는 데 그치지 않고 **코드로 강제**해 권위가 코드에 있게 한다(선언만 두면 슬라이스가 반대로 가도 안 잡힌다 — ADR-20260712가 실증한 드리프트). 세 축으로 realize된다:

- **(a) origin-keyed recognizer.** 한 호출이 API 호출로 인식되는 것은 그 callee가 `IMPORTS` 엣지를 통해 **인식된 프레임워크/라이브러리 origin으로 해석될 때뿐**이다. 프로젝트-로컬/상대 import(`./client`)로 해석되는 callee는 인식하지 않는다. 인식은 **resolved import origin에서 발화**하고 `x.get(...)` 호출 구문에서 발화하지 않는다.
  - `src/palimpsest/extract/http_origins.py:74-89` `is_recognized_call(base, import_specifier)` — no-binding → 등록된 전역(`fetch`)만; bare/pkg specifier → 등록 origin일 때만; relative(`./` `../`) → **미인식**(프로젝트 래퍼, 공개된 gap).
  - `src/palimpsest/extract/ecmascript.py:352` — walker가 `is_recognized_call(base, self.local_imports.get(base))`로 게이트, 인식된 호출만 `api_call_qualified_name`으로 ApiCall 방출.
- **(b) 명시적·확장가능 registry.** 인식 대상은 휴리스틱이 아니라 검토 가능한 목록이다. 라이브러리 하나 추가는 한 줄 튜플 항목이고, 인식 규칙 자체는 불변 — 프로젝트 패턴의 우발적 인식을 **구조적으로** 차단한다.
  - `src/palimpsest/extract/http_origins.py:43` `HTTP_CONSTRUCTS: tuple[HttpConstruct, ...]` frozen 튜플(현 슬라이스: `fetch` 전역, `axios`/`node-fetch` 패키지). JVM 구성물(`RestTemplate`/`WebClient`/Feign)은 JVM recognizer가 소비할 때 같은 한 줄 방식으로 추가한다.
- **(c) ac-4 negative test.** 원칙을 회귀-방지로 단언한다: 프로젝트 래퍼 경유 호출은 ApiCall이 아니고, 래퍼 안의 raw 호출만 인식된다.
  - `tests/extract/test_extract_ecmascript_apicall.py:79-91` `test_project_local_wrapper_call_is_not_apicall` — `import { api } from './client'; api.get('/wrapped')` → `apicall:GET /wrapped` **미방출**(래퍼 레벨 gap); 같은 `./client` 안의 raw `fetch('/api/inner/' + u)` → `apicall:GET /api/inner/{}` **방출**(gap은 래퍼이지 그 안의 원시 호출이 아니다).

## 근거 (rationale)

- **온톨로지 오염 = host-neutral 위반의 모델링 판본.** 프로젝트 자체 래퍼를 인식하면 그 프로젝트에만 참인 패턴이 온톨로지에 박힌다. ADR-20260712 결정 1(b)가 소비자 개념 비내부화를 요구한 것과 동일한 근거로, 프로젝트 패턴도 비내부화한다 — 둘 다 "특정 맥락에만 참인 것을 보편 온톨로지에 새기지 않는다"이다.
- **미인식은 날조 회피이지 정보 손실이 아니다.** 래퍼 레벨을 gap으로 두는 것은 "지어내지 않는다" 원칙과 정합한다(ADR-20260713-multilang의 "JS엔 타입주석 없으니 DEPENDS_ON 날조 대신 아예 미방출"과 동형). 래퍼 안의 raw 호출은 여전히 인식되므로 실제 신호는 보존되고, 손실되는 것은 래퍼 한 겹의 재라벨링뿐이다.
- **판별 질문은 registry 경계 논쟁을 흡수한다.** "axios/node-fetch/ky/got 어디까지 포함하나"(intent unknown)는 이 원칙의 변경이 아니라 **적용**이다 — 널리 쓰이는 라이브러리면 registry 한 줄 추가, 프로젝트 발명이면 배제. 경계가 확장가능 설계라 위험이 낮은 이유가 여기 있다.
- **기각 — 자체 래퍼를 휴리스틱으로 인식.** `.get`/`.post` 같은 메서드명 휴리스틱으로 래퍼를 인식하면 프로젝트마다 다른 우발적 패턴에 온톨로지가 종속되고, host-neutral이 깨진다. origin-keyed 규칙은 인식을 resolved import origin(보편적으로 검증 가능한 사실)에만 결박해 이 결합을 원천 차단한다.

## 관계

- `ADR-20260712-palimpsest-identity-host-neutral-generative-curator`의 **연장·실현**이다. 그 ADR 결정 1(b)·(c)(소비자 개념·산출물 비내부화)를 온톨로지 모델링 경계로 구체화한다: 프로젝트 자체 코드 패턴 = 비내부화 대상.
- `ADR-20260706-generative-curator-direction`(no-laundering)과 정합: 미인식/미매칭은 inferred 날조가 아니라 정직한 gap으로 드러낸다(완전성 미주장, ac-6).
- `ADR-20260713-multilang-depends-on-name-local-boundary`의 "지어내지 않는다"(정적 타입 없는 언어엔 DEPENDS_ON 없음)와 동일 정신 — 프로젝트 래퍼엔 ApiCall 없음.
- 같은 work item의 `ADR-20260713-endpoint-framework-neutral-generalization`(Endpoint 일반화 + CALLS_API)이 이 판별 원칙의 지배를 받는다: Endpoint/ApiCall/CALLS_API는 프레임워크/표준 구성물이라 1급, 프로젝트 래퍼는 배제.

## 철회·변경 조건 (change_condition)

- **프로젝트 자체 패턴을 인식해야 할 명시적 사용자 요구**가 생기고 그 가치가 host-neutral 비용을 넘을 때만 재론한다(예: 특정 사내 표준 클라이언트를 1급 지원). 그 전까지는 정직한 gap을 유지한다 — 편의를 위해 조용히 프로젝트 패턴을 인식으로 끌어들이지 않는다.
- **래퍼 관통 정밀 해석기**가 도입되어(현재 static lower-bound gap) 프로젝트 래퍼 내부의 raw 호출 URL을 데이터플로우로 복원하게 되면, "래퍼 레벨은 gap"이라는 전제(그리고 그 gap의 필요성)를 재검토한다.
- **registry 멤버 추가/삭제**(어떤 라이브러리까지 "널리 쓰임"으로 볼지)는 이 원칙의 변경이 아니라 **적용**이다 — change_condition을 트리거하지 않는다. 판별 질문 자체가 바뀔 때만 이 ADR을 재론한다.
