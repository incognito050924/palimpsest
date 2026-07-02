# ADR-20260702-risk-designdecision-load-contract — Risk·DesignDecision 적재 계약: ADR-20260701을 1급 inferred 시맨틱 엔티티(노드+엣지)로 일반화(로더 유예)

- 식별자: `ADR-20260702-risk-designdecision-load-contract` (파일명 = 불변 식별자)
- 상태: proposed (계약 초안 — 로더·노드·엣지 미구현. 외부 생산자 확정 시 active로 승격, 아래 change_condition)
- 날짜: 2026-07-02
- work item: wi_260702w4l (Risk/DesignDecision 적재 계약 초안; wi_260702jiu에서 이어짐)
- 관계:
  - `ADR-20260701-semantic-layer-load-contract`를 **일반화(generalize·refine)**한다 — supersede 아님. 그 ADR은 의미층을 "요약(Summary)-of-target"으로 실현했고, 이 ADR은 같은 계약 원칙(provider-free·근거결박·`edge_kind='inferred'` 분리·provenance·atomic 거부)을 **1급 inferred 엔티티(자체 정체성을 가진 노드 + inferred 엣지)**로 확장한다. 그 ADR line 35가 범위 밖으로 뒀던 "설계결정/위험 대상"을 이 ADR이 계약 수준에서 명세한다.
  - `ADR-20260702-communityreport-load-contract`와 **형제**다 — 둘 다 유예된 inferred 층을 계약화한다. 단 CommunityReport는 Summary를 그대로 재사용(target=`Community`)하는 얇은 정련이고, 이 ADR은 **새 노드 라벨·엣지 타입을 신설**하는 확장이다.
  - `ADR-20260701-v1-ontology-recall-reframe` §결정3(생성형·inferred 층 유예)이 유예한 층에 속한다 — 이 ADR은 그 유예를 *계약 수준*에서만 해제하고, 실코드(로더·노드·엣지)는 외부 생산자 확정까지 유예 유지.
  - `DESIGN.md` §2/§2-bis 온톨로지 초안(엔티티 `DesignDecision(↔ADR)`·`Risk/Finding`; inferred 엣지 `DECIDES`/`SUPERSEDES`/`RISKS`/`ADDRESSES_RISK`)을 **적재 계약으로 구체화**한다.

## 맥락

의미층 첫 적재(Summary/SUMMARIZES, ADR-20260701)와 Community 구조(ADR-20260702-community)·그 report 계약(ADR-20260702-communityreport)까지는 모두 "기존 노드에 **붙는**" 것이었다. 그러나 `DESIGN.md` §2 온톨로지는 자체 정체성을 갖는 1급 시맨틱 엔티티 — 설계 결정(`DesignDecision`)과 위험(`Risk/Finding`) — 을 예고한다. 이들은 요약이 아니라 **새 노드**이고, 코드·서로에게 **새 inferred 엣지**(어떤 코드를 결정하는가, 무엇이 위험한가)로 연결된다.

이 노드 타입은 아직 KG에 부재하고 생산자도 없다(`DESIGN.md` §6 유예 #3). palimpsest는 provider-free이므로 "이 코드가 위험하다 / 이것이 설계 결정이다"라는 **판정·생성은 외부**여야 한다. 게다가 대상 코퍼스(EcoleTree Java monolith)엔 구조화된 결정 기록이 없고 palimpsest 추출기는 Java 전용(`src/palimpsest/extract/java.py`)이라 코드에서 결정론적으로 뽑아낼 수도 없다. 따라서 이들은 외부 생산자가 만드는 inferred 엔티티다. 이 ADR은 그 **적재 계약**을 확정해 외부 생산자에게 무엇을 emit할지 규약을 주되, 노드/엣지/로더 코드는 앞세우지 않는다(빈 선반 금지).

## 결정

Risk·DesignDecision 적재 계약을 다음으로 결정한다. `ADR-20260701`의 계약 원칙을 1급 inferred 엔티티로 일반화한다.

1. **provider-free 유지.** "위험이다 / 결정이다"의 판정·생성은 전적으로 외부 생산자(예: ditto) 책임이다. palimpsest는 적재·형식강제만 하며 LLM을 호출하지 않는다.

2. **새 inferred 노드 라벨 + namespace 격리 id.** 노드 라벨 `Risk`, `DesignDecision`(코드 라벨과 구분). id는 namespace 격리 — `risk:<sha256>`, `decision:<sha256>` — 로 코드 `qualified_name`(Java FQN/경로)과 절대 충돌하지 않는다(`summary:`/`community:` 선례, code=SoT `kg/summary.py`·`kg/community.py`). id 원료(멱등 rebuild용)는 생산자가 주는 안정 키(예: 정규화 title+source_commit)로 하되, 정확한 원료는 로더 구현 시 확정(유예).

3. **inferred 엣지 + `edge_kind='inferred'`.** 엣지 타입(`DESIGN.md` §2/§2-bis): `DECIDES`(DesignDecision→코드 노드 또는 다른 결정), `SUPERSEDES`(DesignDecision→DesignDecision), `RISKS`(Risk→코드 노드), `ADDRESSES_RISK`(DesignDecision→Risk). 모두 `edge_kind='inferred'`로 적재하며 `deterministic ⊎ inferred == total ∧ NULL==0` 규약을 그대로 지킨다.

4. **전용 inferred 로더 — generic deterministic ingest 재사용 금지.** 이들은 결정론 IR/ingest 경로로 적재하지 **않는다**. 그 경로는 `edge_kind='deterministic'`를 자동 세팅하므로(code=SoT `kg/ingest.py`, `kg/community.py` 주석) inferred 층을 세탁한다. Summary 선례처럼 endpoints를 미리 resolve하고 `edge_kind='inferred'`로 MERGE하는 **전용 로더**를 둔다(silent MATCH-no-op 금지).

5. **근거결박(grounding) — 노드는 떠 있으면 안 된다.** Risk/DesignDecision의 모든 code-facing 엣지(`RISKS`/`DECIDES`의 코드 대상)는 실재 그래프 노드로 resolve돼야 하고, **각 노드는 ≥1개의 resolve되는 grounded 엣지**를 가져야 한다(근거 0개의 떠 있는 판정 노드 금지). 미해소 시 **entity-atomic 거부**(실패한 엔티티만 거부, 나머지 적재 — Summary의 summary-atomic 규약 확장). SUPERSEDES/ADDRESSES_RISK 같은 엔티티-간 엣지는 대상 엔티티가 같은 배치 또는 기존 그래프에 있어야 한다.

6. **provenance·신선도 (Summary 선례 그대로).** inferred 층 provenance는 `generator`/`model` non-null + `created_at`(외부 생성 시각) + `source_commit`(대상 코드 커밋). `confidence`는 **선택**(선례상 누락해도 거부하지 않음 — code=SoT `ir.py`·`kg/summary.py`). `author`는 inferred 층에 싣지 **않는다** — 저자성은 결박된 결정론 코드 노드의 provenance에 있고, 판정의 출처는 `generator`/`model`로 귀속한다(Summary 선례가 inferred 층에 author를 안 싣는 것과 동일). `code_bound_at` = 결박된 코드 노드의 `committed_at`(신선도는 생산자 벽시계가 아니라 코드를 따른다). 외부 판정은 `semantic_verdict` 필드 재사용 가능(생산자 self-confidence와 분리, ADR-20260701 #1 선례).

7. **회상 노출 — 분리 채널·순회 격리.** Risk/DesignDecision는 items 순회로 누출되지 않게 `DECIDES`/`RISKS`/`SUPERSEDES`/`ADDRESSES_RISK`를 traversal 화이트리스트(`DEFAULT_RELATIONS`)에서 제외하고, Summary의 'summaries'처럼 **분리 채널**('risks'/'decisions' 또는 통합 'inferred')로 노출한다. 전용 진입점(`recall_risk` 등)은 구현 선택 — 유예.

8. **로더·노드·엣지 구현은 유예.** 이 ADR은 **적재 규약만** 확정한다. IR/로더 확장(`Risk`/`DesignDecision` 모델, 엣지 상수, 전용 로더, 회상 채널)·payload 생산은 **외부 생산자가 확정돼 실제 payload가 생길 때** 착수한다. 계약만 세우고 코드는 세우지 않는다(빈 선반 금지).

## 근거 (rationale)

- **일반화, 무-중복**: `ADR-20260701`의 4중 계약(provider-free·grounding·inferred 분리·provenance)과 Summary 로더 선례(전용 로더·namespace id·atomic 거부·code_bound_at 결박)를 그대로 재사용한다. Risk와 DesignDecision를 한 ADR로 묶는 이유: 적재 계약 골격이 동일하고 엔티티별 차이는 **엣지 집합뿐**이다. 계약은 하나의 결정(1급 inferred 엔티티 적재 패턴)이고 두 엔티티는 그 인스턴스다.
- **세탁 금지가 핵심**: generic deterministic ingest에 얹으면 `edge_kind='deterministic'`로 오염된다. 판정(위험/결정)은 본질상 inferred이므로 전용 로더로 분리 적재해야 규약이 산다.
- **떠 있는 판정 노드 금지**: 근거 0개의 Risk/DesignDecision는 검증 불가능한 prose다. ≥1 grounded 엣지를 강제해 판정을 실코드에 결박한다(Summary의 claim-grounding을 엔티티에 적용).
- **provider-free 정합**: 판정·생성은 밖, palimpsest는 ingest·형식강제만 — 전 ADR 기조와 동일.
- **빈 선반 회피**: 로더/노드/엣지를 앞세우지 않고 계약만 확정해, 외부 생산자에게 타깃을 주면서도 소비자 없는 코드를 만들지 않는다(핸드오프 §4 준수).
- **기각한 대안 ①**: Risk/DesignDecision를 결정론 IR에 얹기(Community처럼). 기각 — Community 멤버십은 코드에서 결정론적으로 *계산*되지만, "위험/결정"은 코드에서 계산되지 않는 **판정**이라 inferred다. deterministic 층에 얹으면 세탁이다.
- **기각한 대안 ②**: Summary로 표현(Risk를 "위험을 서술하는 요약"으로). 기각 — Risk/DesignDecision은 자체 정체성·엔티티-간 관계(SUPERSEDES/ADDRESSES_RISK)를 갖는 1급 엔티티라, target 하나에 붙는 Summary 모델로는 그 그래프 구조를 표현 못 한다.

## 유예·범위 밖 (명시)

- **로더·노드·엣지 구현** — IR의 `Risk`/`DesignDecision` 모델·엣지 상수, 전용 inferred 로더(endpoint resolve + entity-atomic 거부 + `edge_kind='inferred'` MERGE), 회상 분리 채널·진입점. 외부 생산자 확정 시 착수(change_condition).
- **id 원료 확정** — 멱등 rebuild용 안정 id 원료(정규화 title+source_commit 등). 로더 구현 시 확정.
- **외부 생산자 파이프라인** — Risk/DesignDecision 판정·생성(LLM/분석). palimpsest 밖(provider-free).
- **DesignDecision 결정론 분리** — 대상 코퍼스에 *구조화된* 결정 기록(ADR류 파일)이 있으면 DesignDecision *노드*는 결정론 추출 가능(그 노드는 deterministic 층), 단 `DECIDES` *엣지*는 여전히 inferred(Community의 detection/report 분리 선례). 현재 대상엔 그런 기록이 없어 노드·엣지 모두 inferred로 둔다(change_condition).
- **추가 inferred 엣지** — `DESIGN.md` §2-bis의 `CAUSALLY_RELATES`/`RELATES_TO`·`CONFLICTS_WITH` 등. 필요해지면 이 계약을 확장.
- **provider-free 완화 여부** — 범위 밖(별도 결정, ADR-20260701 입장 유지: hard invariant).

## 철회·변경 조건 (change_condition)

- **활성화(proposed → active)**: 외부(ditto 측) 생산자가 Risk 또는 DesignDecision payload를 실제 생성하기로 확정되면, 이 계약대로 IR/로더/엣지/회상 채널을 구현하고 검증(테스트 + 독립 verify) 후 상태를 active로 올린다.
- **DesignDecision 결정론화**: 대상 코퍼스에 구조화된 결정 기록이 나타나면 DesignDecision 노드의 결정론 추출을 재검토한다(Community 선례, DECIDES 엣지는 inferred 유지).
- **엣지 집합 재검토**: `CAUSALLY_RELATES`/`RELATES_TO`/`CONFLICTS_WITH` 등 추가 inferred 관계가 필요해지면 계약을 확장한다.
- **엔티티 분리 재검토**: Risk와 DesignDecision의 적재 규약이 실사용에서 유의하게 갈리면 ADR을 둘로 분리한다(현재는 골격 공유라 하나).
