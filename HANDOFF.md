# HANDOFF — palimpsest (cross-session)

다른 세션/PC 이어받기용. `.ditto/local/`은 gitignore라 안 넘어오므로 남은 작업은 **코드·계획 SoT 기준**으로 적는다. 이 문서는 배경 지침이지 권위가 아니다 — **계획 SoT는 `.ditto/knowledge/DESIGN.md`**, 사실·동작은 `src/palimpsest/`·ADR, 합의된 의도·수용기준은 work item. 새 세션에서 grep/test로 재확인할 것.

## 0. 사용자 목표 (북극성)
- **DESIGN.md 로드맵 전체를 끝까지 완주** (여러 세션 마라톤).
- **불변식(잠긴 결정, ADR): provider-free** — palimpsest는 LLM을 절대 호출하지 않는다. inferred 층의 *생성·판정*은 전부 외부(ditto 등), palimpsest는 *적재·형식강제·회상*만.
- **⚠ 실행 방식(사용자 강조, 2회):** "슬라이스로 쪼개 유예하지 말라. 제안 순서대로 = 목록 전체를 자율 루프로 계속 내려가라. 한 항목 끝내고 '다음 뭐 할까요'로 멈추는 것 자체가 fragmentation." → 다음 세션도 **합의된 목록을 항목마다 멈추지 말고 실행**. 멈춤은 진짜 intent 갈림·C-결정·비가역 위험일 때만, 그때도 그 항목만 표시하고 가능하면 다음 실행 가능 항목으로. (memory: `no-slice-fragmentation`)

## 1. 전파 상태 (먼저 볼 것)
- **resume**: 브랜치 **`feat/community-node-wi2607010n6`**, tip **`d06ec12`**. main보다 **21 ahead, 0 behind**, clean. 히스토리 재작성 없음. **push 안 함**(사용자: 나중에, 사용자 결정). 이 HANDOFF 커밋도 **push 안 됨** — 다른 PC로 넘기려면 사용자가 브랜치를 push해야 도달.
- origin: `github.com/incognito050924/palimpsest.git`. **코퍼스 repo EcoleTreeSystems 읽기 전용 — git 작업 금지.** 이 PC 경로: `/Users/ecoletree/dev/project/java/workspace/EcoleTreeSystems`(벤치마크용).
- **인터프리터(이 PC)**: `~/.pyenv/shims/python`(3.13.5). `.venv` 없음. 설치: `~/.pyenv/shims/python -m pip install -e ".[test]"`.
- 테스트: `DITTO_AUTOPILOT_BYPASS=1 ~/.pyenv/shims/python -m pytest -q` (Docker 필요 — testcontainers Neo4j; `open -a Docker`). 현재 **118 passed**.

## 2. 이번 세션 landed (feat 브랜치, ce1d406 이후 12커밋 = 6 WI + 초반 recall 진입점)
"A 목록의 완결 가능·값 결정 불필요 항목을 제안 순서대로 완주"한 아크. 각 WI: lightweight path + TDD + fresh-context reviewer 독립검증 + ADR/DESIGN 실현 반영 + feat/docs 2커밋.
- `97fd03a`/`9cf60aa` **inferred 회상 전용 채널**(wi_260702tad): `recall_risk`/`recall_decision`(forward) — 엔티티 id→grounded 대상.
- `26dd75e`/`578aa80` **설계위험 감지 slice 2**(wi_260702qe3): main `recall`/`recall_community`/`expand`에 `risks`/`decisions` 채널(역방향, `summaries` 미러). `_result` 6→8키.
- `2034c51`/`2d87f66` **신선도 2축 결정-계보**(wi_260702c2m, ADR-20260702-decision-lineage-freshness): DesignDecision `valid_from`/`valid_to`, SUPERSEDES=invalidate(전이력 보존), `live`=valid_to null. 회상 decisions 채널 노출.
- `940b890`/`dc2771b` **CommunityReport 표면화**(wi_260702dbu): `recall_community`가 멤버 결박 Summary(CommunityReport)를 'summaries' 채널로.
- `eda55d6`/`0c0c517` **inferred 엣지 확장**(wi_260702rnu): `CAUSALLY_RELATES`/`RELATES_TO`/`CONFLICTS_WITH` 순수 inferred 엣지 로더 `kg/relation.py` + 회상 'relations' 채널. `_result` 8→9키.
- `2a5af09`/`d06ec12` **backfill 전 git 이력**(wi_260702asn, ADR-20260702-backfill-history-capture): `backfill(driver, repo_path)` — `git archive`로 커밋 트리 materialize(비-mutating) 후 extract→ingest replay. CLI `backfill --repo`.

`_result` 형식(9키): `{items, sources, summaries, risks, decisions, relations, gaps, confidence, expand_handle}`. 전 inferred 엣지는 `DEFAULT_RELATIONS`/`REL_TYPES` 제외(items 누출 없음). 테스트 88→118 passed.

## 3. 남은 작업 (완주까지) — DESIGN §6/§7 기준
**A 목록의 "완결 가능·값 결정 불필요"는 소진.** 남은 A 항목은 모두 설계/가치 결정이 선결이거나 heavy:
- **Reconcile 본격(다음 순서) — BLOCKED on intent.** "브랜치 간 신선도 중재"인데 **KG에 브랜치 차원이 전혀 없다**(검증: `grep -rin branch src/palimpsest` = 0건, IR 노드에 branch 속성 없음, 노드는 branch 무관 id MERGE). 결정 필요: ① 브랜치 모델(노드/엣지 태깅 vs 브랜치별 서브그래프 — 새 온톨로지 + branch-aware ingest) · ② 출력 의미(병합 뷰/diff/우선순위 랭킹) · ③ 우선순위(개인↔팀) 판정이 구조적(provider-free)인가 생성 필요(provider-free 완화 = 잠긴 결정)인가. **사용자 intent 없이는 큰 서브시스템을 추측 빌드 = 과잉 위험. 사용자에게 정의 받고 착수.** (씨앗=slice 2의 CONFLICTS_WITH + decision-lineage는 이미 있음.)
- **cross-repo**(다중 저장소 id 네임스페이싱 = 설계 fork) · **agent-trace**(Conversation/AgentTrace 스키마 미정) · **전체 온톨로지**(§2 나머지 엔티티/엣지 선택) · **CPG 확장**(cross-branch dataflow, Reconcile 브랜치 모델과 겹침) — 모두 설계/가치 결정 필요.
- **벤치마크** = 지금 바로 clean-buildable한 유일 항목: ingest·회상·재구축·메모리 실측(스파이크 `docs/spikes/db-substrate-spike.md` §4 지표). 코퍼스 이 PC에 있음. 값 fork 없음(기존 파이프라인 측정). *(vector 검색은 미존재 — 임베딩 C-결정.)*

**B. 외부 생산자 필요(provider-free):** 요약/report/risk/decision/relation **실생성**, 내용(semantic) 검증 판정 하네스. 로더·회상은 전부 실현, 실데이터만 밖.

**C. 사용자만 결정:** 노출 형태(MCP/스킬/pluggable) · 임베딩 설계(부착대상/차원/하이브리드) · provider-free 완화 여부(현재 hard invariant). 여러 A/B 슬라이스의 전제 — 특히 임베딩.

## 4. 알려진 잔여 갭 (전부 low, 각 ADR change_condition에 기록)
- 회상 채널 `limit`는 (entity,ref) row 단위 bound(distinct entity 아님, summaries 선례).
- multi-flag Risk / multi-target Decision `code_bound_at`·relation `code_bound_at`은 `sorted(...)[0]`/source 앵커(엣지별 대상 아님).
- decision-lineage: 다중 supersede 시 valid_to는 last-loaded 승(rf-1); out-of-order created_at 시 interval 역전(rf-2, `live`는 정확).
- inferred relation: self-loop 허용·symmetric 관계 directed 저장(생성자 책임).
- backfill: 커밋별 버전드 스냅샷 노드 없음(HEAD projection만); per-commit 변경 diff 링크 없음; 빈 repo 0-커밋 경로 미검증(graceful).
- same-batch 엔티티 resolution(Decision SUPERSEDES/ADDRESSES_RISK) · CommunityReport orphan(멤버십 변경 시).

## 5. 운영 교훈 (이 세션 검증 흐름 — 재사용)
- **슬라이스 = lightweight work item + TDD**로 몰았다. 큰 구현(backfill)은 **implementer 서브에이전트에 위임**(코드+테스트만, ADR/DESIGN은 coordinator가), 이후 **fresh-context reviewer가 독립 검증**(코드 정독 + 테스트 재현 + 계약 대조). **서브에이전트 "성공" 보고는 증거 아님** — reviewer의 재현 테스트·diff가 증거. reviewer가 finding 잡으면 **inline으로 닫고**(테스트 추가) 재확인.
- **ac 증거 결박**: `ditto verify <wi> --criterion <ac> -- <cmd>`. 전 AC pass여야 `ditto work done`. `--risk` 붙인 WI는 heavy-close 게이트 — 리뷰+증거로 동등 rigor면 `--override-heavy --reason "..."`(감사 기록).
- **drift 감사(필수)**: 실현 반영 후 `grep -n "유예\|proposed\|🔶\|미실현"`로 DESIGN·ADR 전수. 상단 관계 bullet·흩어진 §2/§2-bis 마커를 자주 놓친다. `ditto knowledge adr-check`는 파일명/id 정합만 검사.
- **커밋 분리(Tidy First)**: behavioral(코드+테스트)과 docs(ADR/DESIGN)를 별 커밋으로.

## 6. 금지 (scope creep)
- **슬라이스 쪼개 유예 금지(사용자 강조)** — §0 참조.
- EcoleTreeSystems git 작업 금지. 완료분(위 6 WI) 재구현 금지.
- provider-free 위반 금지 — 판정/생성을 palimpsest 코드에 박지 말 것.
- code = SoT: `src/palimpsest/`가 권위. DESIGN §6 = 계획, ADR = 결정.

## 7. 새 세션 첫 확인
1. `git status` → 브랜치 `feat/community-node-wi2607010n6`, tip `d06ec12`, 21 ahead of main, clean.
2. `open -a Docker` 후 `~/.pyenv/shims/python -m pip install -e ".[test]"` → `DITTO_AUTOPILOT_BYPASS=1 ~/.pyenv/shims/python -m pytest -q` → **118 passed**.
3. 다음 작업 = **Reconcile 정의를 사용자에게 받아 착수**(§3, intent 선결) 또는 사용자가 **벤치마크** 등 다른 항목 지정. 정의 없이 Reconcile 추측 빌드 금지.
4. (선택) 오래된 draft `wi_260626v8v`·`wi_2606264gw`는 superseded — `ditto work abandon` 정리 후보.
