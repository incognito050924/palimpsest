# HANDOFF — palimpsest (cross-PC)

다른 PC/클론 이어받기용. `.ditto/local/`은 gitignore라 **넘어오지 않는다** — intent.json·autopilot.json·coverage.json·premortem-findings.md 전부 원본 PC에만 있다. 그래서 남은 작업은 **코드 SoT 기준**으로 적고, 진행 중인 Reconcile 계획의 핵심을 아래에 **직접 인라인**한다(다른 PC는 autopilot 그래프를 재개할 수 없고, 이 문서 + 코드로 재계획해야 한다). 이 문서는 배경이지 권위가 아니다 — 사실·동작은 `src/palimpsest/`·ADR, 로드맵은 `.ditto/knowledge/DESIGN.md`. 새 PC에서 grep/test로 재확인할 것.

## 0. 북극성
- **DESIGN.md 로드맵 완주**(여러 세션 마라톤).
- **불변식(잠긴 ADR): provider-free** — palimpsest는 LLM/네트워크를 절대 호출하지 않는다. inferred 층 생성·판정은 전부 외부, palimpsest는 적재·형식강제·회상만.
- **실행 방식(사용자 강조):** 목록을 멈추지 말고 내려가되 진짜 intent 갈림·비가역 위험에서만 멈춤(no-slice-fragmentation).

## 1. 전파 상태 (먼저 볼 것)
- **resume**: 브랜치 `feat/community-node-wi2607010n6`. 이 핸드오프 커밋이 tip. 히스토리 재작성 없음.
- **넘어오지 않는 것**: `.ditto/local/` 전체. 진행 중 work item `wi_260702y0d`(Reconcile)의 intent·autopilot·pre-mortem 산출물은 새 PC에 **없다** — `ditto autopilot status`로 재개 불가. §3 인라인 계획으로 재수립하거나, 원본 PC에서 이어가라.
- **인터프리터/테스트(PC마다 다름)**: pyenv 또는 repo `.venv`(py3.12/3.13) — PC에 맞게. Docker 필요(testcontainers Neo4j; `open -a Docker`). 실행: `DITTO_AUTOPILOT_BYPASS=1 <python> -m pytest -q`. 최근 그린 스위트 ~119 passed.
- 코퍼스(벤치마크용, PC마다 경로 다름): `EcoleTreeSystems`(425 commits). **읽기 전용, git 작업 금지.**

## 2. 이번 세션 landed
**코드 커밋 0** — 이 세션은 Reconcile의 **planning 단계 전체**(§3)를 했고 산출물은 `.ditto/local/`(비-git)에 있다. 코드는 한 줄도 안 바뀌었다.
동반 push되는 이전 세션 커밋(이전엔 미push):
- `91891e6` feat(bench) — as-built 4지표 벤치 하네스 + 결과
- `1d0c207` docs(spike) — §4.1 실측 + ingest superlinear finding
- `c9d3169` perf(kg) — ingest 관계 MERGE 끝점 MATCH 라벨화(AllNodesScan→IndexSeek, ~9.5×)
- `f542eaa` docs(spike) — §4.1 인덱스 수정 반영 + before/after 실측

## 3. 진행 중: Reconcile 슬라이스 (wi_260702y0d) — planning 완료, 구현 착수 전
로드맵 다음 항목. **이전 핸드오프의 "Reconcile BLOCKED on intent"는 이번 세션에 해소됨.** 아래가 `.ditto/local`에만 있는 계획의 코드-용어 재수립본이다.

### 3.1 확정 intent (사용자 확인됨)
사용자 선택 **N개 git 브랜치**를 **코드+의미 양층**에서 **N-way 대등**(특권 브랜치 없음) 비교, 회상 시 브랜치 차이·신선도 순위 노출.
- 판정: 코드·시간 층은 **구조 결정론 계산**, 의미층 우선순위는 **외부 판정 결박**(출처+confidence, palimpsest 미생성).
- 대상 브랜치는 **사용자 명시 선택**(모든 ref 자동 금지 — 품질).
- **단일 HEAD projection ADR(ADR-20260702-backfill-history-capture) supersede** — 브랜치 스코프 노드 정체성 도입(비가역, 사용자 informed 확정).
- AC: ac-1 브랜치별 버전 공존 · ac-2 단일브랜치 동작 불변(회귀 게이트) · ac-3 N-way 대등 회상 · ac-4 의미 우선순위 외부결박·LLM 0 · ac-5 git 재구축·전이력보존 · ac-6 지정 브랜치만.

### 3.2 계획(10노드): 코드 지점
`design-identity`(브랜치 스코프 정체성 **id 차원**) → `design-capture`·`design-legacy`·`design-priority` → `impl-capture`(`ir.py`,`kg/ingest.py`,`backfill.py`,신규`reconcile.py`) → `impl-recall`(`recall/graphrag.py`) → `impl-cli`(`cli.py`) → `review-identity` → `verify-regression`·`verify-reconcile`.

### 3.3 pre-mortem 핵심 위험 (구현 전 반드시 반영, 전부 code-grounded)
- **BLOCKER — inferred grounding**: inferred 층(risk/summary/decision/CONFLICTS_WITH)이 전부 **bare node id로 grounding**(`kg/risk.py:113-117`, `recall/graphrag.py:100-113`). 브랜치 스코프 id가 엣지-only(노드 id=qualified_name 유지)면 bare-id MERGE 무조건 SET(`kg/ingest.py:59-71`)이 캡처 순서로 **공유 노드를 붕괴**(last-write-wins) → ac-1/ac-3 위반 + **의미층 grounding 소멸**. ac-2 단일브랜치 스위트는 통과하며 이걸 **가린다**. → 정체성은 **반드시 id 차원**, 멀티브랜치 grounding **명시 검증**.
- git **option-injection**: `-x`/`--upload-pack=` 브랜치명이 옵션 파싱(shell=False는 못 막음). `provenance.py:20-25`/`backfill.py:46,60`에 `--` 구분자 없음. → ref를 `--`/`--end-of-options` 뒤로 또는 `rev-parse --verify`.
- **PII**: `author`=`%ae`(이메일) raw 저장(`provenance.py:29`,`kg/ingest.py:98`). omission 가드는 기존 회상 내부 prose뿐(`graphrag.py:199`) — 신규 reconcile 표면 미상속. → 어느 채널도 `%ae` 미노출.
- **부분 캡처**: N개=N 독립 트랜잭션(`kg/ingest.py:196-197`) → M<N을 완전본으로 표시. → M-of-N 정직성 manifest.
- **shallow**: shallow clone → 이력 무성 절단(`backfill.py:43-49`, ac-5 위반). → `rev-parse --is-shallow-repository` precheck.
- **time**: `committed_at`=`%cI` 원문 문자열(tz 오프셋). 사전식 정렬 시 크로스-타임존 순위 오류. → UTC instant 정규화.
- **비용**: N개 브랜치 각 full-ancestor replay → 공유 base N배 재추출. → merge-base 이후만 또는 extract-once.
- **CONFLICTS_WITH**: inferred(`ir.py:73-74`)라 provider-free reconcile이 **새로 못 만듦** — 기존 것만 표시 또는 구조적 코드 diff.
- **lifecycle-gc**: 브랜치 ref는 가변(rebase/삭제) — 삭제/축소 후 재실행 시 stale 브랜치 노드 잔존(src에 DELETE 0). → scoped-rebuild/reaper.
- **크로스브랜치 누수**: 무방향 CONTAINS hop(`graphrag.py:80-88`)이 공유 unscoped 조상 통해 타 브랜치로. → 브랜치 격리 검증.
- **입력**: 미존재/중복 브랜치 정직 거부(raw traceback 금지), N=0 처리.

### 3.4 다음 (코드 용어, 새 PC에서 재확인)
원본 PC면 `ditto autopilot next-node --workItem wi_260702y0d`로 `design-identity`부터. 새 PC(.ditto/local 없음)면 §3.1–3.3으로 재수립 후 착수. 어느 쪽이든 **provider-free·ac-2 회귀금지·id-차원 grounding 보존**이 불변 제약.

## 4. Gotchas
- Neo4j 5 비밀번호 최소 8자. testcontainers 잔여 정리: `docker ps -q --filter ancestor=neo4j:5-community | xargs -r docker rm -f`.
- Neo4j 5 무라벨 속성 인덱스 없음 — 끝점 MATCH엔 라벨 필수.
- `bench/results/*.log`는 gitignore(JSON만 추적).
- `.ditto/local/` 비-git — 이 문서가 유일한 cross-PC 채널.
