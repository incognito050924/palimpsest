# ADR-20260702-communityreport-load-contract — CommunityReport 적재 계약: ADR-20260701 적재 계약을 target=Community로 정련(로더 실현)

- 식별자: `ADR-20260702-communityreport-load-contract` (파일명 = 불변 식별자)
- 상태: active (계약 실현 — 로더 구현·fixture 검증됨: wi_260702smx. 실제 외부 producer DATA는 provider-free상 외부이며 로더 활성화 전제 아님 — Summary ADR 선례)
- 날짜: 2026-07-02
- work item: wi_260702jiu (CommunityReport 외부 생산자 적재 계약 초안)
- 관계:
  - `ADR-20260701-semantic-layer-load-contract`를 **정련(refine)**한다 — supersede 아님. 그 ADR의 3중 형식(근거결박·`edge_kind='inferred'` 분리·provenance)·summary-atomic 거부·'summaries' 분리 채널·회상 LLM-free 불변식을 **그대로 채택**하고, 그 ADR이 범위 밖으로 뒀던 "커뮤니티 대상"(line 35)만 이 ADR이 **CommunityReport에 한해 해제·명세**한다.
  - `ADR-20260702-community-deterministic-structural`의 change_condition(line 36 — "CommunityReport 도입 시 그 층은 `ADR-20260701`의 적재 계약을 따른다")을 **이행하는 계약**이다. 그 ADR #6이 유예한 생성형 report의 *적재 규약*을 이 ADR이 확정하고 **로더까지 실현**했다(wi_260702smx) — 그 change_condition은 로더 실현으로 이행됐고, 실데이터 생성만 provider-free상 외부로 남는다.
  - `ADR-20260701-v1-ontology-recall-reframe` §결정3(생성형·semantic 요약 유예)이 유예한 층에 속한다 — 이 ADR은 그 유예를 계약 수준에서 해제하고 **로더를 실현**했다(멤버십-grounding, code=SoT); prose 생성만 provider-free상 외부.

## 맥락

Community **구조**(멤버십)는 실현됐다(`ADR-20260702-community-deterministic-structural`, code=SoT `src/palimpsest/kg/community.py`): Class 수준 연결 요소를 결정론적으로 `Community` 노드 + `(:Class)-[:MEMBER_OF]->(:Community)`로 분할한다. 남은 것은 각 community가 "무엇에 관한 묶음인가"를 설명하는 **CommunityReport 요약 prose**다 — 이는 LLM 생성물(inferred)이라 palimpsest의 provider-free 불변식상 **외부 생산자**가 만들어야 한다.

여기 실제 공백이 있다: `ADR-20260701-semantic-layer-load-contract`는 적재 계약을 세웠으나 그 범위(line 35)에서 "커뮤니티/설계결정/위험 대상"을 명시적으로 제외했고, `ADR-20260702-community-deterministic-structural`(line 36)은 CommunityReport를 `ADR-20260701`로 **라우팅만** 하고 유예했다. 즉 **어느 ADR도 Community를 대상으로 하는 report의 적재 규약을 실제로 명세하지 않았다.** 이 ADR이 그 규약을 확정해, 외부 생산자가 무엇을 emit해야 하는지 계약을 주고 이후 구현을 unblock한다 — 단 코드(로더/노드타입)를 앞세워 "빈 선반"을 만들지 않는다.

## 결정

CommunityReport 적재 계약을 다음으로 결정한다. 별도 규약을 신설하지 않고 **`ADR-20260701-semantic-layer-load-contract`의 적재 계약을 채택**하되, 대상이 코드 노드가 아니라 `Community` 노드라는 데서 오는 차이만 정련한다.

1. **provider-free 유지.** report prose 생성은 전적으로 외부 생산자(ditto 측)의 책임이다. palimpsest는 적재·형식강제만 하며 LLM을 호출하지 않는다.

2. **와이어 형식 = Summary 재사용.** 외부 payload는 기존 요약 wire shape(`ir.Summary.to_dict` — `target_id`/`claims`/`generator`/`model`/`source_commit`/`created_at`, 선택 `prompt`/`confidence`)를 그대로 쓴다. 유일한 차이: `target_id`가 `community:<sha256>`(Community id)다. 새 payload 스키마를 만들지 않는다. (근거: 기존 로더 `_resolve`가 label-free `MATCH (n {id})`라 Community id도 그대로 resolve된다 — code=SoT `src/palimpsest/kg/summary.py`.)

3. **엣지 = SUMMARIZES 재사용(`edge_kind='inferred'`).** report→Community 엣지는 새 엣지타입을 신설하지 않고 `SUMMARIZES`(inferred)를 재사용한다. `deterministic ⊎ inferred == total ∧ NULL==0` 규약을 그대로 따른다. `ADR-20260702-community-deterministic-structural` line 36의 "report를 `edge_kind='inferred'`로 적재"와 정합.

4. **근거결박(grounding) 정련 — 멤버십 결박.** report의 각 claim은 ≥1 `source_ref`를 갖고, 그 ref가 **대상 Community의 멤버 Class(또는 그 멤버 안의 Method 등 코드 노드)로 resolve**돼야 한다. 즉 community report는 자기가 요약하는 community의 실제 멤버에 근거해야 한다(무관한 노드로의 근거 세탁 금지). 이는 `ADR-20260701`의 "ref가 실재 그래프 노드로 resolve"를 CommunityReport에서 "ref가 **대상 community의 멤버**로 resolve"로 강화한 refinement다. 미해소 시 summary-atomic 거부(실패한 report만 거부, 나머지 적재) — `ADR-20260701` 규약 그대로. *(멤버십 검사 실현: `_in_community` in `src/palimpsest/kg/summary.py`.)*

5. **신선도 결박(`code_bound_at`).** `code_bound_at` = 대상 Community 노드의 `committed_at`(그 community가 materialize된 커밋; `augment_communities`가 corpus-level provenance로 stamp하고 `Provenance.committed_at`은 non-null — code=SoT). 신선도는 두 갈래다:
   - ⓐ **같은 Community id가 더 최신 커밋에 재materialize**(멤버십 불변, 결박 커밋만 전진)되면 기존 recall stale 판정(대상의 현재 `committed_at` ≠ report의 `code_bound_at`)이 그대로 걸린다 — 새 기계 불필요.
   - ⓑ **멤버십이 바뀌면** id가 `community:` + sha256(정렬 멤버)라 다른 Community 노드가 되고, 옛 report의 `SUMMARIZES` 대상이 사라진다(**orphan**). 이건 committed_at 불일치가 아니라 *대상 부재*다 — **현재 recall stale 판정은 대상 부재를 stale로 치지 않고**(대상 행이 없으면 stale=False; code=SoT `src/palimpsest/recall/graphrag.py`), report claim이 살아남은 멤버 Class에 결박돼 있어 orphan report가 fresh로 노출될 수 있다. 따라서 **orphan report 처리(대상 Community 부재 시 거부/숨김)는 유예된 로더·회상 배선의 명시적 과제**로 남긴다(아래 유예·change_condition). ⓐ만 "공짜"이고 ⓑ는 아니다.

6. **회상 노출 = 'summaries' 분리 채널 재사용.** report는 기존 'summaries' 채널로 노출한다(Summary와 동일 취급, target이 Community일 뿐). 병합 prose 없음. `SUMMARIZES`·`MEMBER_OF` 모두 traversal 화이트리스트에서 제외 유지 — 일반 순회로 걸어지지 않는다. *(`recall_community`가 멤버 반환 시 그 community의 report를 곁들이는 통합은 **실현** — wi_260702dbu: 멤버 items로 'summaries' 채널 재사용, main recall과 동일 역방향 조회.)*

7. **로더 실현(2026-07-02, wi_260702smx).** #4의 멤버십-grounding을 포함한 로더가 구현·fixture 검증됐다(아래 실현 절). 회상은 기존 'summaries' 채널 재사용으로 노출된다. 남은 것은 실제 외부 producer의 payload 생산(provider-free상 palimpsest 밖)과 orphan 처리(유예, 아래). `recall_community` 통합은 실현(wi_260702dbu — 멤버 결박 Summary를 'summaries' 채널로 표면화, 104 passed).

## 실현·검증된 사항 (code = SoT, 동작은 코드가 권위 — 여기 prose로 이중화하지 않음)

- 멤버십-grounding: `_in_community` + load 루프의 `community:` 게이트 in `src/palimpsest/kg/summary.py` — Community 대상 report의 각 claim ref가 대상 community의 멤버 Class(또는 그 멤버가 CONTAINS하는 노드)로 resolve되는지 검사, 비멤버면 entity-atomic 거부.
- 와이어·엣지·신선도: Summary wire(`target_id=community:<sha>`) 재사용, `SUMMARIZES` `edge_kind='inferred'`, `code_bound_at`=대상 Community `committed_at` — 전용 코드 없이 기존 로더 경로.
- 회상: 기존 'summaries' 분리 채널로 노출(멤버 Class 회상 시 report surface), items 누출 없음.
- 테스트: `tests/kg/test_community_report.py`(적재·멤버십 거부·비-community 회귀) · `tests/recall/test_recall_community_report.py`(summaries 채널 surface). 전체 59 passed, provider-free(fresh-interpreter probe 유지).

## 근거 (rationale)

- **공백 메우기**: `ADR-20260702-community-deterministic-structural` line 36이 CommunityReport를 `ADR-20260701`로 라우팅했으나, `ADR-20260701` line 35가 community 대상을 제외해 실제 규약이 비어 있었다. 이 ADR이 그 규약을 확정한다.
- **최소·무-이중화**: Summary wire shape·로더·`SUMMARIZES`·'summaries' 채널을 재사용하면 새 온톨로지/엣지/채널이 없다. 기존 로더가 target 해소에 label-agnostic이라 Community 대상은 구조 변경 없이 되고, CommunityReport 고유로 더할 것은 **멤버십-grounding 검사** 하나뿐이다. 새 라벨/엣지 신설은 inferred 층 기계를 중복시켜(drift·표면 증가) 의미 이득 없이 비용만 늘린다.
- **provider-free 정합**: 판정·생성은 밖, palimpsest는 ingest·형식강제만 — `ADR-20260701`·`ADR-20260702` 전체 기조와 동일.
- **신선도 — 부분만 공짜**: 같은 id 재materialize(멤버십 불변, 커밋 전진, #5 ⓐ)는 기존 recall stale 판정에 그대로 편승한다. 그러나 멤버십 변경이 만드는 orphan(대상 부재, #5 ⓑ)은 현재 recall이 stale로 잡지 못한다 — 그 처리는 유예된 로더/회상 과제로 명시했다(과장 금지).
- **빈 선반 회피**: 로더를 앞세우지 않고 계약만 확정해, 외부 생산자에게 구체적 타깃(무엇을 emit할지)을 주면서도 소비자 없는 코드를 만들지 않는다.
- **기각한 대안**: 새 노드 라벨 `CommunityReport` + 새 엣지(예: `REPORTS_ON`) 신설. 기각 — Summary가 이미 "대상에 대한 외부 근거결박 prose"를 모델링하므로, target이 Community인 Summary가 곧 community report다. 별도 라벨은 이득 없이 중복. *(단 report-수준 고유 속성이 필요해지면 재검토 — change_condition.)*

## 유예·범위 밖 (명시)

- ~~로더 구현~~ → **실현**(2026-07-02, wi_260702smx): 멤버십-grounding·Summary 재사용·회상 배선 완료(위 실현 절).
- **orphan report 처리** — 멤버십 변경으로 대상 Community가 사라진(#5 ⓑ) 옛 report의 거부/숨김. 현재 recall stale 판정은 대상 부재를 stale로 치지 않으므로 로더/회상 배선에서 별도 처리해야 한다(자동으로 해결되지 않음).
- **외부 생산자 파이프라인** — community별 report prose 생성(LLM). palimpsest 밖(provider-free).
- **`recall_community`와의 통합 — 실현**(wi_260702dbu): 멤버 반환 시 그 멤버들에 결박된 Summary(그 community의 CommunityReport 포함)를 'summaries' 채널로 곁들인다(main recall과 동일 역방향 조회, items 누출 없음). 테스트 `tests/recall/test_recall_community_report.py::test_community_report_surfaces_via_recall_community`.
- **report-수준 속성**(community-wide risk score 등) — Summary 스키마에 없는 필드. 필요 시 라벨/엣지 재검토(현재는 Summary 재사용).
- **provider-free 완화 여부** — 이 ADR 범위 밖(별도 결정, `ADR-20260701`과 동일 입장: 현재 hard invariant 유지).

## 철회·변경 조건 (change_condition)

- **활성화(→ active) — 충족**(2026-07-02, wi_260702smx): 로더(멤버십-grounding)·회상 배선을 구현·fixture 검증해 상태를 active로 올렸다. 실제 외부 producer DATA는 provider-free상 외부이며 활성화 전제가 아니다(Summary ADR 선례: 로더 active + fixture 검증, 번들 producer 없음). `ADR-20260702-community-deterministic-structural` line 36의 change_condition은 로더 실현으로 이행됐고, 실데이터·orphan 처리만 남는다.
- **grounding 정련 재검토**: 멤버십-grounding(ref가 대상 community 멤버로 resolve)이 실사용에서 과도하게 엄격/느슨하면 재검토. *(알려진 잔여 갭[low, 계약 허용]: 중첩 Java 클래스는 Class→Class `CONTAINS`를 갖지만 community 탐지는 CONTAINS를 무시하므로, 다른 community에 속한 중첩 inner Class가 그것을 CONTAINS하는 멤버 Class의 report grounding으로 인정될 수 있다 — 좁은 grounding-laundering. 실사용에서 문제되면 CONTAINS 경로를 멤버 Class로만 제한하도록 정련.)*
- **엣지/라벨 재검토**: CommunityReport가 Summary에 없는 report-수준 속성을 요구하면 별도 라벨/엣지 신설을 재검토(현재는 Summary 재사용).
- **회상 노출 충족**(2026-07-02, wi_260702dbu): `recall_community`가 멤버 결박 Summary(CommunityReport 포함)를 'summaries' 채널로 표면화하도록 통합했다(104 passed). orphan 처리는 여전히 아래 유예.
- **orphan report 처리 결정**: 로더/회상이 대상 Community 부재 report(#5 ⓑ)를 어떻게 다룰지(거부 vs 숨김 vs 재생성 트리거) 실착수 시 결정.
