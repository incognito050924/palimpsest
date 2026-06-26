# ADR-20260626-foundational-architecture — palimpsest 정초 아키텍처: Knowledge Graph 본체 + GraphRAG 회상층

- 식별자: `ADR-20260626-foundational-architecture` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-06-26
- work item: wi_2606263sn (정초 deep-interview)

## 맥락

palimpsest는 greenfield(정초) 프로젝트다. deep-interview(wi_2606263sn)에서 사용자가 목적을 교정했다 — "단순 의사결정 context 메모리가 아니라, 프로젝트/프로덕트의 온톨로지 또는 지식그래프 기반 장기기억으로 모든 것을 다뤄야 한다." 사용자 명시: "나의 그림에서는 Knowledge Graph도 중요해."

즉 palimpsest의 본질은 결정 로그를 쌓는 메모리가 아니라, 프로젝트 라이프사이클 전체(의도·결정·코드·관계)를 표현하는 지식그래프 기반 장기기억이다. 이 ADR은 그 정체성을 정초 아키텍처로 못 박는다.

## 결정

4개 사항을 하나의 정초 아키텍처로 결정한다.

1. **본체 = Knowledge Graph.** 장기기억은 엔티티 + 타입 있는 관계 + 온톨로지 + provenance(출처) + 신선도를 담는 지식그래프다. 단일 중심 단위는 없다 — 모든 엔티티가 1급이다. (사용자가 "1급을 하나만 두는 게 어딨어. 모든 엔티티가 1급"이라 명시.)
2. **회상·합성 = GraphRAG.** KG 위에서 그래프 탐색 + 벡터 + LLM 합성으로 근거 결박 답을 만든다. GraphRAG는 KG를 전제로 그 위에 얹히는 층이다 — 경쟁이 아니라 의존이다. 벡터는 보완(하이브리드 검색)이다.
3. **이력 전부 보존.** 채택된 결정뿐 아니라 버려진 대안·중단된 접근·이전 결정까지 1급으로 보존한다. (사용자: "최신 결정만 유지하면 이전에 했던 잘못된 결정으로 회귀할 수도 있잖아. 전부 유지해.") palimpsest 이름과 정합한다 — 지워진 이전 층이 그 아래로 비친다.
4. **캡처 자동 기본.** 자동 흡수가 기본이고, 실시간·배치·사용자 지시 캡처가 섞인다.

## 근거 (rationale)

- **Python GraphRAG 생태계 지배력.** GraphRAG·RAG·임베딩·LLM 합성·그래프 구축 생태계가 Python에 압도적으로 몰려 있어, KG 본체 + GraphRAG 회상 모델을 가장 적은 구조 복잡도로 실현한다.
- **git = SoT.** 원본은 git이고, 그래프 DB는 재구축 가능한 projection(투영)이다. 따라서 본체가 KG라는 정체성과 특정 DB 선택은 분리된다.
- **할루시네이션 최소화.** 생성형 출력은 출처 + 모르는 것(gap) + confidence로 사실과 분리한다(근거 결박). 구조 신호를 퀄리티로 둔갑시키지 않는다.
- **context rot 회피.** 장기기억을 한 번에 로드하지 않고 필요한 부분만 점진적으로 회상한다(GraphRAG 점진 회상).
- **VISION.md(정초 SoT)와 정합.** 5기능(Preserve·Relate·Recall·Curate·Reconcile)이 이 아키텍처 위에서 성립한다.

## 철회·변경 조건 (change_condition)

- DB substrate 스파이크(wi_2606264gw)가 "단일 DB에서 GraphRAG(그래프 + 네이티브 벡터)가 불가능"을 실증하면 §2의 *구현*은 재론한다. 단 git = SoT라 DB는 교체 가능한 projection이며, KG-본체 / GraphRAG-회상이라는 모델 자체는 유지된다.
- "KG가 본체"라는 정체성은 비전 차원 변경(목적 재정의)에서만 재론한다 — 구현 선택의 변화로는 흔들지 않는다.

## 관계

- 외부 ditto repo의 `ADR-0021`이 정초의 출처였으나(START_HERE §출처), 이 ADR부터 palimpsest는 self-contained하게 자체 결정 기록을 시작한다 — 외부 경로에 권위를 의존하지 않는다.
- VISION.md의 잠긴 결정(①~⑥)과 충돌 없음 — 본 ADR은 그 위에서 KG 본체 / GraphRAG 회상이라는 정초 아키텍처를 구체화한다.
- 후속: wi_2606264gw(DB substrate 택일 스파이크)가 §2 구현의 실측 입력이다.
