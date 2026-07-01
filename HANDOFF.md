# HANDOFF — palimpsest (cross-PC)

다른 세션/PC 이어받기용. `.ditto/local/`은 gitignore라 넘어오지 않으므로 남은 작업은 **코드·계획 SoT 기준**으로 적는다. 이 문서는 배경 지침이지 권위가 아니다 — **계획 SoT는 `.ditto/knowledge/DESIGN.md`**, 사실·동작은 `src/palimpsest/`·ADR. 새 세션에서 grep/test로 재확인할 것.

## 0. 전파 상태 (먼저 볼 것)
- **resume**: `main` 브랜치, `git pull`로 `ac3b857`까지. fast-forward pull(히스토리 재작성 없음).
- origin: `github.com/incognito050924/palimpsest.git` (이 repo만). **코퍼스 repo EcoleTreeSystems(`~/dev/project/java/workspace/EcoleTreeSystems`)에 git 작업 금지 — 읽기 전용.**
- **인터프리터**: 이 PC엔 `~/.pyenv/shims/python` 없음. repo 루트 `.venv/bin/python`(homebrew python3.12, `pip install -e ".[test]"`). `.venv`는 gitignore.
- 테스트: `DITTO_AUTOPILOT_BYPASS=1 .venv/bin/python -m pytest -q` (Docker 데몬 필요 — testcontainers Neo4j; `open -a Docker` 후 ~20초). 현재 **47 passed**.

## 1. 이번 세션에 landed (pushed, daf799d→ac3b857)
- `6197c80` recall correctness(#5 label-free MATCH id 결정성, #6 순회 server-side bound) + 외부 요약 실적재 CLI `load`.
- `e51a1b2`+`2324e38` **#2 요약 durability**: git-tracked `summaries/` SoT + `load <dir>` 재구축, Neo4j drop→reload 멱등.
- `7e11979`+`9fa50fe` **#4 stale 감지(detect-only)**: 회상 summaries 채널에 `stale` flag(대상 committed_at ≠ 요약 code_bound_at). 자동 재생성은 provider-free 충돌로 경계 밖.
- `f1074ad`+`ac3b857` **#1 semantic 검증 — verdict-ingest 배선(부분)**: 외부 판정자(ditto)가 만든 verdict를 `Summary.semantic_verdict`(생성기 confidence와 분리)로 ingest·annotate. unfaithful도 로드+flag(거부 아님). provider-free 유지. **판정 하네스·라벨 코퍼스·per-claim은 후속(ditto 측)**.
- `156110b`/`fc8d62d` **DESIGN.md를 계획 SoT로 승격·이동**(`docs/DESIGN.md`→`.ditto/knowledge/DESIGN.md`) + 진행 반영.

## 2. 남은 작업 — #3 요약 대상 확장 (유일 잔여, deep-interview 급)
유예 4개 중 #2·#4·#1(배선) 완료. **#3만 남음.** `.ditto/knowledge/DESIGN.md` §6 유예 테이블의 유일 항목.

### #3 = Risk/Decision/Community 노드타입 신설
- **사용자 결정(이 세션)**: "지금 노드타입 신설"을 선택함 — 단 아래 위험을 알고도 강행.
- **빈 선반 위험(§4-3)**: 그 노드를 채울 **생산자가 아직 없다**. 노드타입만 만들면 채울 데이터 없는 빈 선반. → deep-interview에서 **생산자 계약(누가 그 노드를 만들어 넣나)**을 함께 정해야 함.
- **Java 전용 추출기 제약**: `src/palimpsest/extract/java.py`가 `*.java`만 파싱. 자기 repo(Python) 코퍼스 불가.
- **provider-free 유지**: palimpsest는 판정/생성 안 함. Risk/Decision은 의미 판정이라 외부 생성, Community는 그래프 알고리즘(결정적)이라 palimpsest-native 가능 여지 — 이 경계가 deep-interview 결정점.
- **edge_kind**: 새 노드/엣지가 deterministic(구조)인지 inferred(외부 생성)인지 규약(deterministic⊎inferred==total). Community=deterministic 후보, Risk/Decision=inferred.
- **온톨로지 참조**: DESIGN §2-bis에 이 노드들이 🔶(미실현) 제안으로 이미 있음.

### #3 착수 방법
1. `ditto work start`로 #3 work item 등록.
2. `/ditto:deep-interview` — 노드타입 온톨로지 설계 + 생산자 계약 + edge_kind 분류 + (Community 등) palimpsest-native vs 외부 경계 결정. Java-only 제약·빈 선반 위험을 fixed fact로.
3. 결정 후 route: Community(결정적)만 먼저면 lightweight 가능, Risk/Decision(inferred 온톨로지+생산자)까지면 heavy.

## 3. 이 세션 운영 교훈 (다음 세션 참고)
- **autopilot heavy는 국소·가역 작업에 과중**: plan-stage far-field coverage sweep(6축 pre-mortem)이 작은 작업에 불비례하고 relevance 자동 축소가 이 세션에서 안 먹었다. #2/#4/#1 배선은 전부 **lightweight**(deep-interview로 모호성만 해소 → implementer TDD → verify → done)로 순조. #3도 결정이 서면 규모에 맞게 route.
- **provider-free가 방향타**: 유예 항목마다 "판정/생성은 밖, palimpsest는 ingest/detect만"이 반복 패턴. #3도 동일 렌즈.
- 검증은 fresh-context verifier 서브에이전트로(§4-9).

## 4. 금지 (scope creep)
- EcoleTreeSystems git 작업 금지.
- 완료분(#2·#4·#1 배선·recall·load) 재구현 금지.
- #3의 판정/생성을 palimpsest 코드에 박지 말 것(provider-free). Risk/Decision 생산자 계약 없이 노드타입만 만들지 말 것(빈 선반).
- code = SoT: `src/palimpsest/`가 권위. DESIGN/ADR은 계획·결정 기록.

## 5. 새 세션 첫 확인
1. `git pull` → main이 `ac3b857`인지.
2. `open -a Docker` 후 `DITTO_AUTOPILOT_BYPASS=1 .venv/bin/python -m pytest -q` → 47 passed.
3. #3 방향(생산자 계약·native 경계)을 사용자와 확정 후 deep-interview.
