# ADR-20260702-decision-lineage-freshness — 신선도 2축(결정-계보): DesignDecision bi-temporal validity(valid_from/valid_to), SUPERSEDES invalidate=전이력 보존

- 식별자: `ADR-20260702-decision-lineage-freshness` (파일명 = 불변 식별자)
- 상태: active (실현·검증: wi_260702c2m — 로더+회상 구현, 103 passed, fresh-context 독립 리뷰 PASS)
- 날짜: 2026-07-02
- work item: wi_260702c2m (신선도 2축 결정-계보 valid_from/valid_to)
- 관계:
  - `ADR-20260702-risk-designdecision-load-contract`를 **확장**한다(supersede 아님) — 그 ADR이 실현한 `DesignDecision`/`SUPERSEDES` 위에 **계보 신선도(2번째 축)**를 얹는다. 로더가 SUPERSEDES를 적재할 때 피대상 결정의 currency를 결정론적으로 갱신한다.
  - `DESIGN.md` §2/§2-bis 신선도 2축 제안(`valid_from`/`valid_to`=결정-계보 신선도, Graphiti 패턴)을 계약으로 굳혀 실현한다 — DESIGN §8 "ADR 후보(결정-계보)"의 승격.
  - `ADR-20260701-semantic-layer-load-contract`의 provider-free 불변식을 유지한다 — 계보 신선도는 SUPERSEDES **구조**로부터 결정론적으로 계산되며 LLM을 부르지 않는다.
  - 축 1(코드-결박 신선도 `code_bound_at`/회상 `stale`, wi_260701v0q)과 **직교**한다 — 이 ADR은 축 2(결정이 더 최신 결정에 의해 supersede 됐나)만 다룬다.

## 맥락

VISION·DESIGN은 **2축 신선도**를 예고했다: ① 코드-결박(지식이 가리키는 코드가 현재 커밋에 살아있나 — 실현됨) · ② **결정-계보**(이 지식이 더 최신 결정에 의해 supersede 됐나 — "현재 live" 판정, 단 이력은 보존). 축 1만 있던 상태에서 결정은 supersede 돼도 여전히 live처럼 보였다.

전 이력 보존(ADR-20260626)이 잠긴 결정이므로, supersede된 결정을 **삭제하면 안 된다** — 버려진 결정도 회귀 방지를 위해 남긴다. 따라서 "삭제" 대신 "invalidate"가 필요하다: 결정을 유지하되 "더는 현재가 아님"으로 표시한다. `SUPERSEDES` 엣지는 이미 존재하므로(risk-designdecision 로더), 계보 신선도는 그 구조로부터 **결정론적으로 계산**할 수 있다 — 외부 판정 불필요, provider-free 유지.

## 결정

DesignDecision에 bi-temporal validity를 부착한다.

1. **`valid_from`/`valid_to` 노드 속성.** `valid_from`=결정이 현재가 된 시각(=`created_at`), `valid_to`=현재가 끝난 시각(supersede 전엔 `null`). 새 결정 적재 시 `valid_from`=created_at·`valid_to`=null로 **ON CREATE 초기화**한다(노드 재-MERGE가 계보 신선도를 덮어쓰지 않도록 — 그래야 supersede된 결정을 재적재해도 되살아나지 않는다).

2. **SUPERSEDES = invalidate, not delete.** 결정 D1이 D0를 `SUPERSEDES`로 적재되면 D0의 `valid_to`를 **D1의 `created_at`**(D0가 현재가 끝난 시각)로 SET한다. D0 노드·엣지는 **보존**한다(전 이력 보존). 대상은 로더가 이미 resolve+라벨체크(`DesignDecision`)한 뒤라 SET은 항상 materialize한다.

3. **`live` = `valid_to IS NULL`(read-time 파생).** 저장하지 않고 회상 시 계산한다. supersede된 결정은 회상에서 **여전히 표면화**되되(이력 보존) `live=false`로 표시된다 — 소비자가 현재/과거를 구분한다.

4. **회상 노출.** recall `decisions` 채널의 각 항목이 `valid_from`/`valid_to`/`live`를 싣는다(decisions 전용 — risks 채널엔 계보 엣지가 없어 미부착). Summary의 `stale`(축 1)과 나란한 축 2 신선도 신호.

5. **provider-free·결정론.** 계보 신선도는 SUPERSEDES 구조로부터만 계산 — palimpsest는 판정하지 않는다. LLM 0, hermetic 재현.

6. **범위 = DesignDecision.** 축 2는 계보 엣지(`SUPERSEDES`)를 가진 엔티티에만 적용된다. Risk엔 계보 엣지가 없어 해당 없음(구조적으로 배제, 유예 아님).

## 실현·검증된 사항 (code = SoT, 동작은 코드가 권위 — prose로 이중화하지 않음)

- 로더 `src/palimpsest/kg/decision.py`: `_DECISION_MERGE`에 `ON CREATE SET d.valid_from=$created_at, d.valid_to=null`(일반 SET은 이 둘을 안 건드림 — 재적재 비리셋). `_SUPERSEDE_INVALIDATE`(피대상 `valid_to`=superseder `created_at`)를 `_write` 말미에 호출(supersedes 있을 때). 삭제 없음.
- 회상 `src/palimpsest/recall/graphrag.py`: `_DECISIONS_CHANNEL`이 `valid_from`/`valid_to` 반환, `_entity_channel`이 `"valid_to" in row`일 때만 `valid_from`/`valid_to`/`live` 부착(decisions 전용, risks 미부착).
- 테스트: `tests/kg/test_decision.py`(신규 3: live+valid_from=created_at·supersede-invalidate-보존·재적재-비리셋) + `tests/recall/test_recall_design_risk.py`(신규 2: 채널 live 노출·supersede된 것 표면화+not-live). 전체 103 passed, provider-free probe 유지. fresh-context 독립 리뷰 PASS(behavior-risk finding 0).
- 알려진 단순화[low]: 아래 change_condition(rf-1·rf-2) 참조.

## 근거 (rationale)

- **삭제 대신 invalidate**: 전 이력 보존(ADR-20260626 잠긴 결정)이 삭제를 금한다. valid_to로 currency를 끄되 노드를 남기면 "버려진 결정"까지 회상 가능 — 회귀 방지의 핵심.
- **구조로부터 결정론 계산**: SUPERSEDES는 이미 로더가 적재하는 구조적 사실이다. 계보 신선도를 이 구조로부터 계산하면 외부 판정 없이(provider-free) 축 2가 선다. Summary의 `stale`(축 1)이 committed_at 비교로 결정론적인 것과 동형.
- **ON CREATE 가드**: valid_to를 일반 SET에 두면 supersede된 결정의 재적재가 그것을 null로 되살린다(silent resurrection). ON-CREATE-only가 이 회귀를 막는다.
- **기각 대안 ①**: live를 노드에 저장. 기각 — 파생 상태 이중화는 drift 위험(supersede 후 갱신 누락). read-time 파생(valid_to IS NULL)이 SoT 단일.
- **기각 대안 ②**: supersede 시 D0 삭제. 기각 — 전 이력 보존 위반.

## 유예·범위 밖 (명시)

- **CommunityReport orphan / 결정 외 엔티티 계보** — 범위 밖. 이 ADR은 DesignDecision `SUPERSEDES`만.
- **transaction-time(2번째 시간축 전체 bi-temporal 매트릭스)** — 범위 밖. DESIGN "2축"은 코드-결박 + 결정-계보이며 그 둘로 충족. valid-time 계보만 다룬다.
- **valid_to 기반 회상 필터링/시점 질의**("time T에 live였던 결정만") — 유예. 현재는 현재-live 판정(플래그)만; 과거 시점 재구성은 후속.

## 철회·변경 조건 (change_condition)

- **다중 supersede 순서(rf-1)**: 두 결정이 같은 prior를 SUPERSEDES하면 `valid_to`는 **last-loaded**가 이긴다(chronology 아님, `SET` 무조건). 기존 flags[0]/first-code-target 앵커링과 동종 단순화. 다중 supersede가 실사용에서 흔해지면 min(created_at) 기준으로 정련.
- **out-of-order created_at(rf-2)**: superseder `created_at` < prior `valid_from`이면 interval 역전(`valid_to < valid_from`) 가능(외부 created_at 신뢰). `live` 판정은 여전히 정확. 시점 질의(위 유예)가 붙으면 ordering 가드를 추가한다.
- **valid_to 시점 질의 필요 시**: 위 유예의 시점 재구성을 실현하며 interval 정합을 강화한다.
- **Risk 계보 엣지 도입 시**: Risk에 supersede류 엣지가 생기면 축 2를 Risk로 확장한다(현재는 구조적으로 해당 없음).
