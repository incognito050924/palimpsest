# CodeQL HEAD-only 보조 — 실현가능성 스파이크 결과 (재유예)

> **성격**: 실현가능성 스파이크 결과 기록(report). 코드 산출물 없음 — plan-stage pre-mortem이 산출물이다.
> **출처**: work item `wi_260712rf1` · GitHub issue #4 · 권위 ADR: `ADR-20260706-generative-curator-direction` §결정6/§41/§82.
> **상태**: 재유예(re-deferred). ADR-20260706 §41 change_condition 유지.
> 작성: 2026-07-13

## 1. 결론

- 사용자 의도: **실현가능성 스파이크 후 재유예**. "적재 계약까지" 코드 스파이크로 착수했으나, 착수 게이트의 plan-stage pre-mortem(far-field 6축 스윕)이 **코드 한 줄 없이** 실현가능성을 특성화했다.
- **판정**: curate 동형 격리 패턴(격리 opt-in 생산자 → git 선물질화 → 기존 멱등 Risk 로더)은 구조적으로 성립한다. 그러나 security/taint Risk를 **건전하게** 적재하려면 §3의 설계 이슈들을 먼저 풀어야 한다.
- **happy-path 코드는 vacuously green**: 합성 단일 finding fixture는 §3의 hard case(id 충돌·위치 정밀도·다중 흐름·resolver)를 전부 우회하므로, ac-2/ac-3가 green이어도 실효 증거가 못 된다(스윕 4개 중 3개가 독립적으로 경고). → 코드 미작성이 정직한 선택. ADR-20260706 §41 유예 유지.

## 2. C-Q5 정정 (핵심 — 기록된 결정 갱신)

착수 시 잠정 선택은 **"edge_kind='inferred' 유지 + `extracted_by='codeql'` provenance"**였다. pre-mortem이 이를 **unsound로 반증**:

- **id 충돌·세탁**: `risk_id`(`src/palimpsest/kg/risk.py:57-67`)는 `title + source_commit + sorted(flags)`만 해시하고 provenance를 제외한다. `_RISK_MERGE`는 MERGE-on-id + blind `SET`(`risk.py:73-98`)이라, 같은 `(title, source_commit, flags)`의 CodeQL Risk와 LLM Risk가 **동일 노드로 붕괴하고 last-writer-wins로 `generator`/`model`/`extracted_by`가 덮어써진다**. 즉 마커가 정체성 load-bearing이 아니다.
- **write-only 마커**: 회상 채널 `_RISKS_CHANNEL`(`src/palimpsest/recall/graphrag.py:259-272`)은 `edge_kind`를 읽되 `extracted_by`는 읽지 않는다. → 스파이크 범위에서 마커는 **쓰기 전용**이고, C-Q5 구분(코드QL vs LLM)의 종단 검증에는 회상 채널이 필요하다(이 스파이크의 out-of-scope).
- 결론: "provenance-only"는 C-Q5 3안(edge_kind / provenance / subtype) 중 **no-laundering 보장이 가장 약한 안**이다.

**정정된 사운드 방향(사용자 결정, 2026-07-13)**: provenance 속성이 아니라 **Risk subtype 또는 edge-level discriminator**로 CodeQL 결정론적 Risk를 구분한다. (최소안으로 `extracted_by`를 `risk_id` 키에 편입해 공존을 보장할 수는 있으나, property-only의 약점이 남으므로 subtype/edge가 우선.) no-laundering 이분 불변식(`deterministic ⊎ inferred == total`)과의 정합은 un-defer 슬라이스에서 확정한다. ADR-20260706 §82("CodeQL 세부 edge_kind 표기 규칙 유예")의 이행 방향이 이것이다.

## 3. 실현 전 해결 게이트 (un-defer 시 선행조건)

각 항목은 스윕에서 oracle-linked로 확인된 것이다(file:line + AC).

### HIGH
- **SARIF 비결정성 (external-env)**: SARIF는 `invocation.startTimeUtc/endTimeUtc`, 도구/쿼리팩 버전 문자열, 절대 `srcRoot`/artifact URI, 멀티스레드 result 순서 등 환경마다 달라지는 필드를 내장한다. 물질화 전 **정규화**(결정론 키로 정렬, 내장 timestamp/version/절대경로 제거, 리포 상대경로만) 없이는 `ac-2` byte-identical이 flaky. codeql DB 스크래치 디렉터리는 스크래치에만 쓰고 물질화 산출물·워킹트리에서 제외.
- **실패 vs clean 구별 (failure-recovery)**: 생산자가 subprocess exit-code를 검사해 "빌드/`database create`/쿼리 실패(non-zero)"를 "정상 0 findings"와 구별해야 한다. 삼키면 **실패가 '위험 없음'으로 위장**(보안 니치 최악의 false-clean). 물질화는 tmp → `os.replace` 원자 교체(부분/truncated JSON 방지).
- **SARIF→node-id resolver (reuse crux)**: CodeQL은 `file:line`으로 보고하나 로더 `_resolve`(`risk.py:113-120`)는 **branch-scoped node id**(`branch:<b>\x1f<qn>`, `ir.py:32-43`, ADR-20260703)로 grounding한다. 이 매핑 어댑터는 **필수이며 격리 생산자 밖(caller-side)**에 있어야 한다(`ac-1`가 생산자 폐포에 `kg`/`recall` 금지). 계획이 누락했다. resolver 버그와 정당한 미-grounding을 구별하는 fixture 필요(안 그러면 `ac-3`가 엉뚱한 이유로 pass/fail).
- **왕복 필드 부재**: `Risk.to_dict/from_dict`(`ir.py:371-394`)에 discriminator 필드가 없어, git 물질화 왕복에서 마커가 조용히 소실된다(§2 방향의 subtype/edge를 IR에 왕복시켜야).

### MEDIUM
- **다중 흐름 붕괴 (boundary-edge)**: 같은 sink·같은 룰의 서로 다른 taint 흐름이 같은 `risk_id`로 붕괴 → silent undercount(보안 false-negative). 흐름 식별자(예: SARIF `resultIndex`/codeFlow 해시)를 결정론적으로 id에.
- **위치 정밀도 (input-validation)**: SARIF는 expression/statement-level 위치이나 IR 최소 단위는 `Method`(`ir.py:45-53`). 감싸는 Method 노드가 없는 finding은 entity-atomic 거부로 드롭되고, File 노드로 올리면 taint 정밀도(니치의 존재이유)를 잃는다. 매핑 규칙(enclosing-Method / File / reject)을 확정하고 fixture가 no-enclosing-Method 케이스를 포함해야 한다.
- **비트랜잭션 적재 (data-integrity)**: `risk.py`의 `_write`는 노드 MERGE와 엣지 MERGE를 별도 auto-commit `session.run`으로 실행(비트랜잭션) → 둘째 실패 시 floating Risk 노드. 형제 로더 `kg/ingest.py:154`는 `execute_write`로 원자적. 같은 패턴으로 원자화(기존 로더 선재 결함).
- **time-clock**: `created_at` 벽시계를 물질화 payload에 넣으면 같은 HEAD 재스캔이 비-byte-identical(`risk.py:80` MERGE 시 무조건 SET). 결정론 출처(`source_commit` 커밋시각) 파생 또는 payload에서 제외. (`code_bound_at`은 git-파생 결정론이라 무관.)

## 4. 재유예 결정

- ADR-20260706 §41 change_condition((a) 외부도구 비용/신뢰성, (c) 외부-only 품질 미달 등) **유지**. §3의 게이트가 이 트랙 un-defer 시의 구체 선행조건이다.
- host-neutral(ADR-20260712 §결정1): 생산자는 palimpsest 소유 격리 실행으로 확정(ditto 산출 소비 아님). C-Q2 해소.
- 이력 엔진은 tree-sitter spine 유지(C-Q1). CodeQL은 HEAD-only 보조.
- **main 미병합**(코드 없음). 이 문서만 knowledge로 잔존.
