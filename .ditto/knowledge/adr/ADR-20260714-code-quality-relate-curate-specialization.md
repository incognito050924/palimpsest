# ADR-20260714-code-quality-relate-curate-specialization — F-Q4: 코드품질 이해·정규화는 Relate/Curate의 특화이지 새 최상위 정체성이 아니다

- 식별자: `ADR-20260714-code-quality-relate-curate-specialization` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-14
- work item: 이슈 #1 (design-notes 주제 F — 코드베이스 품질 이해·정규화), F-Q4

## 맥락

design-notes 주제 F(= GitHub 이슈 #1)는 "palimpsest가 코드베이스 품질을 이해하고 정규화한다"를 다룬다. F-Q4는 그 상류 교착이었다: 이 능력이 palimpsest의 **새 최상위 목적(정체성)** 인가, 아니면 기존 축의 특화인가. 정체성급 질문이라 아래 facet 작업(F-Q1 산출물, F-Q2 경계, F-Q5 SoT, F-Q6 자기인증)이 모두 여기에 막혀 있었다.

## 결정

코드베이스 품질 이해·정규화는 palimpsest의 **새 최상위 목적(정체성)이 아니라 기존 축의 특화**다:

- **구조 품질 팩트**(콜그래프 지역성·응집·coverage·엣지 정밀도) = **Relate**(관계·구조 이해)의 코드-품질 특화 — detect-only·provider-free.
- **안티패턴·리팩터 후보** = **Curate**(생성형 합성)의 코드-품질 특화 — 외부 생성·근거결박, 자기인증 금지(F-Q6).

## 근거 (rationale)

- **ADR-20260712 정체성 불변식 ① 환경 비종속·"소비자 개념 비내부화"**: "코드 품질"은 소비자(ditto ACG/개발자)의 개념이다. 이를 최상위 정체성으로 내부화하면 불변식 ①을 위반한다 — palimpsest는 특정 소비자 목표에 최적화되지 않은 범용 지식 substrate로 남아야 한다.
- **ADR-20260712 ② 생성형 큐레이터 = 핵심 목적**: 코드 구조를 낫게 "가꾸기"는 이미 생성형 큐레이션 그 자체다 → Curate의 특화이지 새 기둥이 아니다.
- **실증(코드로 답한 F-Q4)**: 이슈 #6 coverage verdict(Summary 품질 검증)와 정밀 spine 증분1의 per-edge resolution 마커(Relate 층 팩트, wi_260713bz4) 둘 다 **새 정체성 없이 기존 층의 특화로 착지**했다 — F-Q4를 이미 코드로 답한 셈이다.

## 귀결 (consequence)

- palimpsest 5기능 모델 개정 불필요.
- 이슈 #1(주제 F)이 정체성급 상류 교착에서 **기존 축 아래 facet 작업으로 강등** → 착수 가능.
- 하위 facet 착지:
  - **F-Q1(산출물)** = Relate 구조 품질 팩트 + Curate 합성.
  - **F-Q2(경계)** = advisory substrate(관측·신호만 제공, 실제 변경은 소비자 몫).
  - **F-Q5** = coverage/응집 facet의 SoT를 이슈 #6에 위임.
  - **F-Q6(자기인증 경계)** = Curate 합성에 적용(자기인증 금지, 근거결박).

## 철회·변경 조건 (change_condition)

어떤 코드-품질 용례가 Relate/Curate 특화로 표현 불가능하고, palimpsest를 코드-품질 전용 목표로 최적화하도록 요구하는 증거가 나오면 이 결정을 재검토한다.

## 관계 (related)

- `ADR-20260712-palimpsest-identity-host-neutral-generative-curator` — 이 결정의 근거가 되는 정체성 불변식(① 환경 비종속 ② 생성형 큐레이터). 본 ADR은 그 불변식을 코드-품질 용례에 적용한 것이며 supersede하지 않는다.
- 이슈 #1 (design-notes 주제 F), 이슈 #6 (coverage/응집 facet SoT), wi_260713bz4 (per-edge resolution 마커 — Relate 층 팩트 실증).
