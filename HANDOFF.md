# HANDOFF — palimpsest (cross-PC)

다른 PC에서 이어받기용. `.ditto/local/`은 gitignore라 넘어오지 않으므로, 남은 작업은 **코드 기준**으로 적는다. 이 문서는 배경 안내이지 권위가 아니다 — 새 PC에서 grep/test로 재확인할 것.

## 0. 전파 상태 (먼저 볼 것)
- **resume**: `main` 브랜치, `git pull`로 `945f010`까지 받는다. **히스토리 재작성 없음** — 평범한 fast-forward pull.
- origin: `github.com/incognito050924/palimpsest.git` (이 repo만). **코퍼스 repo EcoleTreeSystems(`~/dev/project/java/workspace/EcoleTreeSystems`)에는 어떤 git 작업도 금지.**
- **안 넘어오는 것**: `.ditto/local/`(work-item 레코드·autopilot.json·intent.json·coverage.json·completion.json·runtime 로그). 새 PC엔 wi_260701cjf 등 레코드가 없다. → 완료된 slice는 코드로 확인하지, 레코드로 확인하지 않는다.
- **넘어오는 것**: `.ditto/knowledge/`(ADR·glossary·CONTEXT·knowledge.json)는 git-tracked라 travels.

## 1. Landed this session (pushed)
- `ba31639 ditto land wi_260701cjf` — slice 4 코드: `src/palimpsest/{ir.py, kg/summary.py(신규), kg/ingest.py, kg/__init__.py, recall/graphrag.py, cli.py}` + tests. (+876/-24)
- `945f010 docs(knowledge)` — slice 4 지식: `ADR-20260701-semantic-layer-load-contract.md`(신규) + glossary 4용어(Summary·SUMMARIZES·edge_kind='inferred'·의미층) + CONTEXT·knowledge.json·CLAUDE.md 프로젝션.

## 2. 현재 상태 (slice 4 = done, 검증됨)
palimpsest = 코드→KG(Neo4j)→조합형 grounded 회상(v1) 위에 **의미층 적재 계약**(slice 4)이 얹혔다.
- 외부 에이전트가 생성한 "왜·함정" tacit 요약을 받아 `Summary` 노드 + `SUMMARIZES`(edge_kind='inferred')로 적재. **palimpsest 코드는 provider-free(LLM 호출 0)** — 요약 생성은 전부 외부.
- 근거결박: 모든 claim이 ≥1 resolve되는 source ref 강제, 미해소는 **summary-atomic 거부**(무성 드롭 아님). Summary id는 `summary:<sha256>` 네임스페이스(코드 노드 안 가림). provenance: `code_bound_at`=대상 코드 committed_at, `created_at`=생성시각, generator/model.
- 회상: `summaries` **분리 채널**(병합 prose 없음, SUMMARIZES는 traversal 화이트리스트 제외라 items로 누출 불가), 회상 경로 LLM-free 유지. CLI `query`에 SUMMARIES 섹션.
- **테스트 33 passed**(19→33), exit 0. edge_kind 분리는 DB 제약 아니라 writer+테스트로 강제(`deterministic ⊎ inferred == total ∧ NULL==0`).

## 3. 남은 작업 (코드 기준 — 새 PC에서 재확인)
### slice 4 follow-up (유예, 새 slice 후보)
- **내용(semantic) 검증층**: 지금은 형식만(ref resolve). 요약이 근거를 *의미적으로* 뒷받침하는지 판정(사람/eval), 자기저작 코퍼스로 이동. 현재 게이트: `src/palimpsest/kg/summary.py`의 `_structural_reject_reason`(형식 전용) 확인.
- **요약 durability**: 현재 Neo4j-only — `load_summaries`는 payload→Neo4j write만, git 아티팩트 SoT 없음. **Neo4j drop→rebuild 시 요약 소실.** git=SoT 재구축 내구성 필요 시 slice.
- **요약 대상 확장**: 커뮤니티 리포트·설계결정·위험(Risk/Finding).
- **stale 요약 자동 재생성/reconcile**: 지금은 bound 커밋 표시만.

### v1 잔여 (미조치 low findings)
- **recall label-free MATCH id 충돌**: `recall/graphrag.py`의 `_RESOLVE`가 `MATCH (n {id})` 라벨 없이 매칭 — 코드 노드끼리(다른 label·같은 qualified_name) 이론적 충돌. (slice 4는 Summary만 네임스페이스 격리)
- **recall boundedness client-side**: 순회 예산이 클라이언트 쪽 — super-node 폭발 위험.

### 열린 draft work item (레코드는 로컬 전용, 새 PC엔 없음 — 여기 재기술)
- **DB substrate 스파이크**: Neo4j vs Memgraph vs PostgreSQL(pgrouting/AGE). 성능·메모리·재구축 실측 벤치 미측정.
- **docs/DESIGN.md 살아있는 지도**: 최종목표·온톨로지·5기능·로드맵·미결.

## 4. Gotchas (실행)
- 인터프리터: **`~/.pyenv/shims/python`**(pyenv 3.13.5). 시스템 `python`/`python3` 아님.
- 테스트: `DITTO_AUTOPILOT_BYPASS=1 ~/.pyenv/shims/python -m pytest -q`. **Docker 데몬 필요**(kg/recall/e2e는 testcontainers Neo4j). pytest 설정에 pythonpath=src.
- CLI: `NEO4J_URI=... NEO4J_USER=... NEO4J_PASSWORD=... PYTHONPATH=src ~/.pyenv/shims/python -m palimpsest ingest --repo <repo> | query <symbol|file>`.
- Pyright가 `palimpsest.X` import 미해소로 표시하는 건 pyrightconfig 부재 탓 false-positive(전 모듈 공통) — 테스트는 pythonpath=src로 통과.

## 5. 금지 (scope creep)
- **EcoleTreeSystems에 git 작업 절대 금지** — palimpsest repo만.
- v1/slice 4 재구현·확대 금지(done).
- 내용 검증·생성형 요약 생성·cross-repo를 새 slice 스펙 합의 없이 착수 금지.
- code = SoT: `src/palimpsest/`가 권위. VISION/DESIGN은 배경 지도.

## 6. 새 PC 첫 확인
1. `git pull` → main이 `945f010`인지.
2. Docker 데몬 켜고(`open -a Docker`) `DITTO_AUTOPILOT_BYPASS=1 ~/.pyenv/shims/python -m pytest -q` → 33 passed 확인.
3. 다음 slice 방향 사용자와 확정 후 deep-interview(또는 lightweight).
