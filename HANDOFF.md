# HANDOFF — palimpsest (cross-PC)

다른 세션/PC 이어받기용. `.ditto/local/`은 gitignore라 넘어오지 않으므로 남은 작업은 **코드·계획 SoT 기준**으로 적는다. 이 문서는 배경 지침이지 권위가 아니다 — **계획 SoT는 `.ditto/knowledge/DESIGN.md`**, 사실·동작은 `src/palimpsest/`·ADR. 새 세션에서 grep/test로 재확인할 것.

## 0. 전파 상태 (먼저 볼 것)
- **resume**: 브랜치 **`feat/community-node-wi2607010n6`** (main 아님). `git fetch origin && git checkout feat/community-node-wi2607010n6`로 `a512683`까지. main보다 2 커밋 앞. **main 병합은 미정 — 사용자 결정** (PR 또는 fast-forward). 히스토리 재작성 없음.
- origin: `github.com/incognito050924/palimpsest.git` (이 repo만). **코퍼스 repo EcoleTreeSystems(`~/dev/project/java/workspace/EcoleTreeSystems`)에 git 작업 금지 — 읽기 전용.**
- **인터프리터**: 이 PC엔 `~/.pyenv/shims/python` 없음. repo 루트 `.venv/bin/python`(homebrew python3.12, `pip install -e ".[test]"`). `.venv`는 gitignore.
- 테스트: `DITTO_AUTOPILOT_BYPASS=1 .venv/bin/python -m pytest -q` (Docker 데몬 필요 — testcontainers Neo4j; `open -a Docker` 후 ~20초). 현재 **55 passed** (47 baseline + 8 Community).
- **안 넘어오는 것(.ditto/local)**: work item `wi_2607010n6` record(status=done), `autopilot.json`, `intent.json`, coverage run 등. 새 PC엔 없다 — 아래 남은 작업은 코드/DESIGN 기준으로만 판단.

## 1. 이번 세션에 landed (feat 브랜치, 7ff99b7→a512683)
- `727da11` **#3 요약 대상 확장 — Community 노드타입 신설 (behavioral)**: deterministic 구조 묶음, provider-free. Class 레벨 무방향 연결요소(GDS 없이 순수 Python union-by-min-root), `community:`+sha256(정렬 멤버) id로 재빌드 멱등, `(:Class)-[:MEMBER_OF]->(:Community)` `edge_kind=deterministic`. `recall_community` 진입점(멤버 Class bounded·grounded·author-omit·LLM-free, MEMBER_OF는 traversal 화이트리스트 제외). 신설 `src/palimpsest/kg/community.py`, 수정 `ir.py`/`kg/ingest.py`/`kg/__init__.py`/`recall/graphrag.py`/`recall/__init__.py`/`cli.py`. 테스트 `tests/kg/test_community.py`(5)·`tests/recall/test_recall_community.py`(3). ADR `ADR-20260702-community-deterministic-structural` 신설(ADR-20260701-v1과 refine) + DESIGN §2-bis/§6 갱신(Community 구조 ✅ / CommunityReport prose 🔶 분리).
- `a512683` **chore(structural)**: `.gitignore`에 `*.iml`·`*.ditto_bak` 추가.
- heavy path로 진행(deep-interview → coverage sweep → TDD → fresh verifier/reviewer). 4/4 AC evidence-backed pass, work item done(final_verdict=pass).

## 2. 남은 작업 — DESIGN §6 유예 (전부 외부 생산자 의존, inferred)
#3 유예 4개 중 #2·#4·#1·#3 완료. **구조적 유예 항목은 모두 소진.** 남은 건 "판정/생성이 palimpsest 밖"이라 유예된 inferred 층뿐:
- **CommunityReport 요약 prose**: Community 구조(멤버십)는 이번에 실현. 그 묶음이 "무엇에 관한 것인지" 설명하는 요약 글은 LLM 생성이라 provider-free상 **외부 생산자 계약**이 있어야 함. 계약 없으면 빈 선반(#3 Community 부분의 논리적 후속).
- **Risk / DesignDecision 노드타입**: 의미 판정이라 외부 생산자 필요(inferred). 생산자 계약 없이 노드타입만 만들지 말 것(빈 선반).
- 공통 착수 조건: **외부(ditto 측) 생산자 계약이 먼저.** 계약 서면 Community 요약 적재 계약(Summary/SUMMARIZES 선례) 재사용 가능.
- **Java 전용 추출기 제약**: `src/palimpsest/extract/java.py`가 `*.java`만. 자기 repo(Python) 코퍼스 불가 — 테스트 코퍼스는 `tests/**/fixtures`의 Java.

## 3. 이 세션 운영 교훈 (다음 세션 참고)
- **provider-free가 방향타**: 유예 항목마다 "판정/생성은 밖, palimpsest는 ingest/detect만". Community도 구조(deterministic native)와 prose(inferred 외부)를 갈라 구조만 실현했다.
- **autopilot coverage sweep의 `--relevance` 자동 축소가 이 repo에서 안 먹음**: seed 후 각 카테고리를 `coverage-round`로 수동 close 해야 함(skip=out_of_scope+reason+residual_risk, relevant=resolved+`axis_signals.neutrality{opponent_ran,verdict}`). resolved close는 자식이 모두 dry여야 함.
- **autopilot judging 노드(verify/review/docs) evidence 결박**: `outcome=pass` 시 `ac_verdicts`의 pass verdict가 `evidence_refs`(object `{kind,command/path,summary}`)를 carry하지 않으면 G7이 non-contentful로 override하고, `complete`가 AC를 unverified로 남긴다. 병렬 verify∥review는 서로 커버 안 되니 둘 다 evidence 필요. 노드가 이미 passed면 `autopilot.json`의 `status`를 pending으로 바꿔 re-arm 후 재record(CLI에 reopen 없음).
- 검증은 fresh-context verifier 서브에이전트로(§4-9).

## 4. 금지 (scope creep)
- EcoleTreeSystems git 작업 금지.
- 완료분(#2·#4·#1·#3 Community·recall·load) 재구현 금지.
- CommunityReport prose / Risk / DesignDecision을 **외부 생산자 계약 없이 노드타입만** 만들지 말 것(빈 선반). 판정/생성을 palimpsest 코드에 박지 말 것(provider-free).
- code = SoT: `src/palimpsest/`가 권위. DESIGN/ADR은 계획·결정 기록.

## 5. 새 세션 첫 확인
1. `git fetch origin && git checkout feat/community-node-wi2607010n6` → tip이 `a512683`인지. (main 병합 여부 사용자와 확인.)
2. `open -a Docker` 후 `DITTO_AUTOPILOT_BYPASS=1 .venv/bin/python -m pytest -q` → 55 passed.
3. 다음 작업(CommunityReport prose / Risk / DesignDecision)은 **외부 생산자 계약이 선결** — 계약 유무를 사용자와 확정 후 착수.
