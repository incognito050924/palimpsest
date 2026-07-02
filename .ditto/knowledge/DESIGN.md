# palimpsest — 시스템 설계 지도 (계획 SoT)

> **위상:** 이 문서는 palimpsest **계획·로드맵·최종 형상·미결정의 SoT(단일 기준점)**다. "무엇을 어떤 순서로 만들어 최종 형상에 이르는가"는 여기가 권위다.
> **단, 사실·동작·결정의 SoT는 아니다(§4-11 drift 방지):** 구현·동작은 코드(`src/palimpsest/`), 되돌리기 어려운 결정은 ADR(`adr/`), 합의된 의도·수용기준은 work item `intent.json`이 SoT다. 이 문서의 ✅는 그 ADR/코드/intent를 **가리키는 포인터**이며 내용을 이중화하지 않는다. 계획 항목이 결정으로 굳으면 **ADR로 승격**하고 여기 태그를 포인터로 갱신한다 — 그래야 이 지도가 drift 없이 산다.
> **목적:** 슬라이스들이 흩어지지 않게 정렬하는 골격. 전체 목표·온톨로지·아키텍처·기능·로드맵·미결을 한 화면에 둔다.
> **소비자:** palimpsest 개발자/에이전트. **갱신:** 결정·슬라이스·스파이크마다. **폐기 조건:** 계획이 전부 ADR·코드·계약으로 흡수되어 더 가리킬 게 없어지면.
> **정체성·정초 전제:** [`../../docs/VISION.md`](../../docs/VISION.md)(이름·목적·5기능·잠긴 결정). 이 문서는 그 위에서 로드맵·형상을 조망한다.
> work item: `wi_260626v8v`(v0 작성), `wi_26070129t`(계획 SoT 승격·최신화), `wi_2607010n6`(Community 구조 그룹핑 반영). 최종 갱신: 2026-07-02.

**범례:** ✅ 확정(ADR/코드/intent 근거) · 🔶 제안(미확정) · ⬜ 미결(검증/결정 대기)

---

## 1. 최종 목표 / 비전

- ✅ **정체성:** palimpsest는 한 프로젝트/프로덕트의 라이프사이클 전체 — 의도·결정·코드·관계·위험 — 을 담는 **Knowledge Graph 기반 장기기억·지식 큐레이터**다. "의사결정 메모리"는 그 일부일 뿐. (ADR-20260626, [VISION](../../docs/VISION.md))
- ✅ **세 목표:** ① LLM·에이전트 **할루시네이션 최소화**(근거 결박) · ② **context rot 없는 점진 회상**(필요분만, 토큰 1급 제약) · ③ **의사결정·의도 투명화**(숨은 의도 충돌 방지). (VISION)
- ✅ **독립성:** palimpsest는 **standalone**이다. ditto 위에 얹히지 않는다. 개발 단계엔 ditto가 *개발 도구*로 쓰이고(설계에 비침투), 완성 후엔 ditto가 *소비자*가 되어 palimpsest를 호출한다. ditto는 **노출 경계에서만** 고려한다. (ADR-20260626 #1, 잠긴 결정 #4/#5)

## 2. 도메인 모델 / 온톨로지 *(가장 중요 · 가장 미결)*

**원칙 (✅):** KG가 본체. **모든 엔티티가 1급**(단일 중심 단위 없음). **전 이력 보존** — 채택뿐 아니라 버려진 대안·중단된 접근·이전 결정까지(회귀 방지). **provenance(출처)와 신선도가 1급 속성**. (ADR-20260626)

- 🔶 **엔티티 타입(제안, v1 최소 + 확장):** `Commit` · `File` · `Symbol`(Function/Class/Module) · `Change`(diff 단위) · `Branch` · `Author`(사람/에이전트) · `DesignDecision`(↔ADR) · `Requirement` · (확장) `Conversation`/`AgentTrace` · `Community`(구조 그룹핑 ✅ 실현·결정론) / `CommunityReport`(요약 prose 🔶 유예·생성형).
- 🔶 **관계 타입(제안):** `MODIFIES` · `CALLS`/`DEPENDS_ON` · `IMPLEMENTS` · `DECIDES`/`SUPERSEDES` · `AUTHORED_BY` · `RISKS`/`CONFLICTS_WITH` · `EVOLVED_FROM`(이력 계보).
- 🔶 **provenance:** 모든 노드·엣지에 `source`(commit SHA / 문서 / 대화) + `confidence` + `gap`(모르는 것). 생성형 추론은 반드시 이 셋으로 사실과 분리(세탁 금지).
- 🔶 **신선도(2축, 제안):** ① **코드-결박 신선도** — 지식이 가리키는 코드 위치·심볼이 현재 커밋에 살아있나 · ② **결정-계보 신선도** — 이 지식이 더 최신 결정에 의해 supersede 됐나(이력은 보존하되 "현재 live" 판정). VISION의 "커밋 해시 기반 2축 신선도"를 이렇게 구체화 제안.
- ⬜ **미결:** 정확한 2축 정의·계산 · 기억의 입도(granularity) · 임베딩 부착 대상과 차원 · 온톨로지 진화/버전 전략 · `Risk`를 노드로 둘지 관계로 둘지(적재 계약 초안은 **노드 + `RISKS` 엣지**로 제안 — `ADR-20260702-risk-designdecision-load-contract`, proposed).

### 2-bis. node/edge 스키마 — 정적층 ✅ v1 실현 · 의미층 ◐ slice 4 부분 실현 *(2026-07-01, code=SoT)*

> ✅ **정적/결정론 층(v1, ADR-20260701):** `Repo/Package/File/Class/Method`(+`Episode`=commit), `CONTAINS/CALLS/DEPENDS_ON/IMPORTS`, 모든 엣지 `edge_kind='deterministic'`(Neo4j Community라 DB제약이 아닌 **writer+테스트로 강제**), provenance(commit SHA/author)+`code_bound_at`. 회상은 조합형(LLM 없음). → `src/palimpsest/{ir,kg,recall}`.
> ✅ **Community 구조 그룹핑(결정론 층, ADR-20260702-community-deterministic-structural):** Class 수준 무방향 연결 요소를 결정론적으로 분할한 `Community` 노드 + `(:Class)-[:MEMBER_OF]->(:Community)` 엣지(`edge_kind='deterministic'`, LLM·GDS 없음). 재구축 안정 `community:<sha256>` id, IR에 materialize돼 partition 불변식이 카운트, `recall_community` 전용 진입점(bounded·grounded·LLM-free, MEMBER_OF는 순회 화이트리스트 제외). **CommunityReport(요약 prose)는 생성형이라 유예(아래).** → `src/palimpsest/kg/community.py`, `recall_community` in `recall/graphrag.py`, `tests/kg/test_community.py`, `tests/recall/test_recall_community.py`.
> ◐ **의미층 첫 적재 계약(slice 4, ADR-20260701-semantic-layer-load-contract):** `Summary` 노드(외부 생성 tacit 요약, id=`summary:<sha256>` 네임스페이스) + `SUMMARIZES` 엣지(`edge_kind='inferred'`, 첫 적재)가 실현됐다. **palimpsest는 provider-free(LLM 호출 0)** — 요약 생성은 전적으로 외부, 적재 시 근거결박·inferred 분리·provenance를 강제. 회상은 'summaries' 분리 채널로 노출(items 누출 없음). → `src/palimpsest/kg/summary.py`.
> 🔶 **아직 미실현:** `DesignDecision/ADR` · `Risk/Finding` · `CommunityReport` 노드, 그 외 inferred 엣지(`CAUSALLY_RELATES`·`ADDRESSES_RISK`·`DECIDES`·`RELATES_TO`), 요약 durability(git-SoT), 내용(semantic) 검증. (→ §6 로드맵 '유예')

1차 자료 종합 — Glean(pre-compute fact)·HugRAG(unified edge space)·CPG/Joern(type-label overlay)·Graphiti(bi-temporal/Episode). 근거·검증(23 confirmed/2 refuted)·출처 전체: [`research/precompute-hugrag-kg.md`](research/precompute-hugrag-kg.md). **충돌 검토: ADR-20260626과 충돌 없음** — 오히려 각 시스템이 우리 모델 조각을 실증.

- 🔶 **노드 — 정적층(CPG):** `Repo` · `Module/File` · `Type/Class` · `Method/Function` · `Variable/Local` · `CallSite` · `Community`(구조 그룹핑 ✅ 결정론). **의미층:** `Summary`(✅ slice 4)/`CommunityReport`(요약 prose 🔶 생성형) · `DesignDecision/ADR`(🔶) · `Risk/Finding`(🔶) · `Episode/SourceCommit`(✅).
- 🔶 **엣지 — 결정론적 구조(`edge_kind=deterministic`, ✅ v1):** `CONTAINS` · `CALLS` · `IMPORTS` · `DEPENDS_ON` · `MEMBER_OF`(✅ Class→Community) · (제안) `REACHING_DEF`(DATAFLOW) · `INHERITS` · `REF`. **생성형 추론(`edge_kind=inferred`):** `SUMMARIZES`(✅ slice 4) · `CAUSALLY_RELATES`(🔶) · `ADDRESSES_RISK`(🔶) · `DECIDES`(🔶) · `RELATES_TO`(🔶).
- ✅ **정적/생성형 분리(세탁 금지):** ① 별도 edge label + ② 모든 엣지에 `edge_kind = deterministic|inferred` 속성. `deterministic ⊎ inferred == total ∧ NULL==0`을 writer+테스트로 강제(Neo4j Community 제약 부재). slice 4에서 inferred 값 첫 적재.
- 🔶 **provenance·2축 신선도 = 엣지/노드 속성(Graphiti 패턴):** `source`=Episode/commit SHA(provenance) · `valid_from`/`valid_to`=결정-계보 신선도(삭제 대신 invalidate=전이력 보존, 🔶 미실현) · `code_bound_at`=코드-결박 신선도(✅ slice 4에서 대상 커밋 committed_at에 결박) · inferred일 때 `generator`/`model`/`confidence`/`created_at`(✅ slice 4).

## 3. 아키텍처

- ✅ **git = SoT.** KG는 재구축 가능한 projection, 벡터는 보완. (ADR-20260626 #2, 잠긴 결정 #2)
- ✅ **provider-free 불변식(slice 4~):** palimpsest 코드는 LLM을 직접 호출하지 않는다. 생성(요약·검증·재생성)은 전적으로 외부 에이전트, palimpsest는 결과 payload를 근거결박으로 적재·회상만 한다. 테스트는 hermetic(키·네트워크 없이 재현), 회상·적재 경로에 생성형 라이브러리 0을 fresh-interpreter probe로 강제. (ADR-20260701-semantic-layer-load-contract 결정 #1)
- 🔶 **컴포넌트(제안):**
  - **Capture** — git(커밋·diff·브랜치) → 추출 → 중간표현(IR). (확장: 문서·대화·에이전트 트레이스). 자동 기본 + 실시간/배치/명시 혼합.
  - **KG Store** — 그래프 DB(🔶 Neo4j 1차) + 네이티브 벡터 인덱스. provenance·신선도 부착.
  - **Recall/Curate** — GraphRAG: 그래프 탐색 + 벡터 + LLM 합성. 조합형(계보·여정) + 생성형(근거 결박, 외부 생성).
  - **Exposure Adapter** — 얇은 층. 코어를 노출 메커니즘에서 분리.
- 🔶 **데이터 흐름:** `git → Capture → IR → KG build/update(provenance·신선도) → Recall(query) → Curate(synthesize, grounded) → Adapter → 소비자`. 의미층은 `외부 생성 요약 → 적재 계약(근거결박) → KG(inferred) → 회상 summaries 채널`.
- ✅ **노출 독립:** 코어(KG·GraphRAG)는 노출 메커니즘과 무관. 노출은 갈아끼우는 어댑터. ditto 내부 구조에 비의존.

## 4. 기능 명세 (5기능)

각 기능 = 입력 / 동작 / 출력 / 근거결박. 기능 정체성은 ✅(VISION), 상세 동작은 🔶/⬜.

| 기능 | 입력 | 동작 | 출력 | 비고 |
|---|---|---|---|---|
| **Preserve** ✅ | git 변경 | KG 노드·엣지 생성/갱신 + provenance·신선도 | 보존된 KG 조각 | 전 이력 보존 |
| **Relate** ✅ | 엔티티들 | 코드·결정·의도·여정 간 관계 투영 | 질의 가능한 그래프 | 벡터 보완 |
| **Recall** ✅ | 작업 맥락 질의 | 필요분만 점진 회상(🔶 pull 우선, push 확장) | 관련 조각(토큰 예산 내) | context rot 회피 |
| **Curate** ◐ | 회상된 자료 | 조합형(계보·여정 구성, ✅) + 생성형(외부 LLM 합성, 적재만 ✅ slice 4) | 답 + **출처+gap+confidence** | 세탁 금지, provider-free |
| **Reconcile** ⬜ | 브랜치 간 컨텍스트(개인↔팀) | 차이 인정 → 신선도·우선순위 판정 | 무엇이 더 신선/우선인지 | 설계위험 감지(slice 2)가 그 씨앗 |

⬜ 각 기능의 상세 동작·계약은 슬라이스별로 구체화.

## 5. 노출 / 통합

- ⬜ **메커니즘 미결:** MCP 서버 / 스킬 등록 / ditto-pluggable. Python 주언어는 셋 다 가능(MCP Python SDK · 언어 무관 스킬). (VISION §다음단계 #5)
- ✅ **현재 노출:** CLI(`python -m palimpsest ingest|query|load`). **요약 실적재 진입점(CLI `load`)은 실현**(wi_2607016ir, 커밋 `6197c80`) — 외부 생성 요약 JSON payload를 적재(근거결박·summary-atomic 거부 유지, rejections 표면화, provider-free). MCP/스킬은 유예.
- ✅ **소비자 일반화:** 개발자(사람) + 에이전트. ditto는 **첫 소비자일 뿐**, 노출을 ditto에 특화하지 않는다.
- 🔶 **회상 계약(제안):** `query(작업 맥락) → grounded answer(출처+gap+confidence) + 점진 확장 핸들`. 사람용(설명형)·에이전트용(토큰 맞춘 근거 조각) 양면.

## 6. 로드맵 / 슬라이스 계획

- ✅ **v1 = 온톨로지 + grounded 회상 slice** (`wi_2606263sn`, **shipped·검증 통과**, ADR-20260701-v1-ontology-recall-reframe): 코드베이스(EcoleTree Java monolith)를 캡처→KG 온톨로지→GraphRAG로 관련 코드·의존을 **출처 붙여 점진 회상**. 성공 = **온톨로지 구축·동작 보장**(실코퍼스 158파일 e2e + 테스트 + 독립 verify), 회상 퀄리티는 다음. code=SoT: `src/palimpsest/`. *(재프레임 전 v1 후보였던 "설계위험 감지"는 slice 2로 유예.)*
- ✅ **slice 4 = 의미층 첫 적재 계약** (`wi_260701cjf`, **shipped·검증 통과**, ADR-20260701-semantic-layer-load-contract): 외부 생성 tacit 요약을 `Summary` 노드 + `SUMMARIZES`(inferred)로 **근거결박·inferred 분리·provenance 강제**로 적재. provider-free. 회상은 'summaries' 분리 채널. code=SoT: `src/palimpsest/kg/summary.py`.

- ✅ **요약 durability(git-SoT)** (`wi_260701ffv`, **shipped·검증 통과**, 커밋 `e51a1b2`): 외부 생성 요약 payload를 git-tracked `summaries/` 디렉토리에 SoT로 두고 CLI `load <dir>`로 일괄 재적재. 결정적 `summary:<sha256>` id + MERGE 멱등이라 Neo4j drop→reload가 동일 Summary·SUMMARIZES 복원(멱등 rebuild 테스트). provider-free 유지. git=SoT는 ADR-20260626 #2의 요약 적용(ADR-20260701 durability change-condition 충족).
- ✅ **stale 감지(#4, detect-only)** (`wi_260701odo`/`wi_260701v0q`, **shipped·검증 통과**, 커밋 `7e11979`): 회상 summaries 채널의 각 항목에 `stale` bool 노출 — 대상 노드의 현재 `committed_at`이 요약 `code_bound_at`과 다르면(재ingest로 갱신됨) stale=true. 순수 read-side(새 Cypher 왕복 없음), provider-free. **자동 재생성(LLM 필요)은 경계 밖 유지.**
- ✅ **Community 구조 그룹핑** (`wi_2607010n6`, **shipped·검증 통과**, ADR-20260702-community-deterministic-structural): Class 수준 연결 요소를 결정론적으로(union-find, LLM·GDS 없음) `Community` 노드 + `MEMBER_OF`(deterministic)로 분할, `community:<sha256>` 재구축 안정 id, IR materialize로 partition 불변식 포함, `recall_community` 전용 진입점(bounded·grounded·LLM-free). 검증: 55 passed. code=SoT: `src/palimpsest/kg/community.py`, `recall/graphrag.py`. **CommunityReport(생성형 요약 prose)는 유예(아래 #3).**
- ✅ **recall correctness + 요약 실적재 경로** (`wi_2607016ir`, **shipped·검증 통과**, 커밋 `6197c80`): ① recall `_RESOLVE`에 `ORDER BY head(labels(n))` — label-free MATCH id 충돌 해소(#5) · ② `_NEIGHBORS`/`_SUMMARIES`에 `ORDER BY` 뒤 `LIMIT` — 순회 예산 server-side bound, 정상 노드 동등성 유지(#6) · ③ 외부 생성 요약을 실제로 적재하는 CLI `load` 진입점 신설(provider-free 유지). 검증: 전체 39 passed, 4 AC evidence-gated. code=SoT: `src/palimpsest/{recall/graphrag.py,cli.py,ir.py}`. *근거: 의미층 후속(아래 유예)의 실사용 전제(적재 경로)를 먼저 세운다.*

- 🔶 **유예된 의미층 후속** — 실적재 경로로 실제 사용·고통을 확인하고 각 ADR change-condition을 재검토한 뒤 착수:

  | 유예 항목 | 무엇 · 왜 유예 |
  |---|---|
  | **#1 내용(semantic) 검증층** — ◐ 부분 shipped | **배선 실현**(wi_260701ulo, 커밋 `f1074ad`): 외부 판정자(ditto)가 만든 verdict를 `Summary.semantic_verdict`로 ingest·annotate(unfaithful도 로드, 회상 flag 노출), provider-free 유지. **후속(ditto 측)**: 판정 하네스·라벨 코퍼스·per-claim. 코퍼스는 Java 전용 추출기 제약(self-Python-repo 불가). |
  | **#3 요약 대상 확장** | `Risk`/`DesignDecision` 노드로 대상 확대. 그 노드 타입은 아직 KG에 부재·생산자 없음이나 **적재 계약 초안됨**(🔶 proposed, `ADR-20260702-risk-designdecision-load-contract` — ADR-20260701을 1급 inferred 엔티티로 일반화: 새 노드 라벨 `Risk`/`DesignDecision`·inferred 엣지 `DECIDES`/`SUPERSEDES`/`RISKS`/`ADDRESSES_RISK`·전용 로더·grounded 노드 강제). 로더·노드·엣지 구현과 외부 생산자는 유예. *(`Community` 구조 노드는 실현 — 위 roadmap. 각 Community에 붙는 `CommunityReport` 생성형 요약 prose도 적재 계약 초안됨 — `ADR-20260702-communityreport-load-contract`.)* |

  *(#2 durability·#4 stale detect는 shipped — 아래 로드맵 참조. #4의 자동 재생성은 provider-free 충돌로 경계 밖 유지.)*

- 🔶 **이후 슬라이스(확장축)** — 각 슬라이스가 켜는 기능·온톨로지 조각:

  | 슬라이스 | 켜는 것 |
  |---|---|
  | **설계위험 감지**(slice 2) | 구조적 결합 회상 위에 '위험' 판정·표시(Reconcile 씨앗) |
  | 위험판정 **퀄리티** | Curate 정밀도, recall@k, 헛경보 억제 |
  | **push** 능동 경고 | Recall push 트리거·개입시점 |
  | **페르소나** 회상 | 소비자별 뷰(PM/아키텍트/…) |
  | 전체 **backfill** | Capture 소급 발굴(전 git 이력) |
  | **에이전트 트레이스** 캡처 | `Conversation`/`AgentTrace` 엔티티 |
  | **전체 온톨로지** | §2 엔티티·관계 전체 |
  | **Reconcile** 본격 | 브랜치 간 신선도 중재 |
  | 조직·**cross-repo** | 다중 저장소 스코프 |

## 7. 미결 · 검증 대상

- ✅ **DB substrate (v1)** — Neo4j Community 채택·실현(`src/palimpsest/kg`, testcontainers 검증). 단 성능·메모리·재구축 **실측 벤치는 여전히 미결**(스파이크 §4 지표). → `docs/spikes/db-substrate-spike.md` (`wi_2606264gw`).
- ◐ **node/edge 설계** — 정적/결정론층 v1 실현 + 의미층 첫 적재 slice 4 실현(§2-bis). 코드-결박 신선도 **stale 판정은 실현**(wi_260701v0q, 회상 stale flag). 남은 미결: derived/inferred 엣지 생성 메커니즘 · LLM 추론 엣지 precision 가드레일(내용 검증, 유예 #1) · CPG intra-procedural → cross-branch 확장 · stale **재조정=자동 재생성**(provider-free 충돌로 경계 밖). → `docs/research/precompute-hugrag-kg.md`.
- ◐ **스키마 상세** — 정적 엔티티/관계 + Summary/SUMMARIZES + `Community`/MEMBER_OF(결정론 구조 그룹핑, ADR-20260702)는 실현(code=SoT). 의미층 나머지 노드(`Risk`/`DesignDecision`는 적재 계약 초안됨 — `ADR-20260702-risk-designdecision-load-contract`, 노드+inferred 엣지·로더 유예; `CommunityReport` 생성형 요약 prose도 적재 계약 초안됨 — `ADR-20260702-communityreport-load-contract`, 로더 유예)·신선도 2축(`valid_from/valid_to` 결정-계보)은 미결.
- ✅ **요약 durability** — git-tracked `summaries/` SoT + CLI `load <dir>` 재구축으로 **해소**(wi_260701ffv, 커밋 `e51a1b2`): Neo4j drop→reload 멱등 복원. 다중 대상 코드베이스별 요약 분리·요약 payload 생산 파이프라인은 후속 여지.
- ✅ **recall boundedness/정확성** — `_RESOLVE` label-free id 충돌·순회 예산 client-side를 **해소**(wi_2607016ir, 커밋 `6197c80`): 결정적 tie-break + server-side Cypher `LIMIT`. `_SUMMARIES` row-bound 정책(병리적 고요약 노드에서 distinct summary vs row 단위)은 후속 여지.
- ⬜ **임베딩 설계** — 부착 대상·차원·하이브리드 검색 구성(v1 미포함).
- ✅ **데모 코퍼스 (v1)** — `EcoleTreeSystems`(Java monolith) 확정·사용. 두 브랜치 설계위험 시나리오는 slice 2에서. *(주의: EcoleTreeSystems repo에 대한 git 작업 금지 — 읽기 전용 코퍼스.)*
- ⬜ **성능** — ingest·순회·벡터검색·재구축·메모리(스파이크 §4 지표, 미측정).
- ⬜ **노출 형태** — MCP/스킬/pluggable 택일.
- ⬜ **provider-free 경계** — 내용 검증·자동 재생성이 제기하는 "완화 여부"는 열린 결정(현재는 hard invariant 유지).

## 8. ADR 인덱스 (권위 — 이 문서의 ✅가 가리키는 곳)

- ✅ `ADR-20260626-foundational-architecture` — KG 본체 + GraphRAG 회상층, 전 이력 보존, 자동 캡처, git=SoT. (active) → [`adr/ADR-20260626-foundational-architecture.md`](adr/ADR-20260626-foundational-architecture.md)
- ✅ `ADR-20260701-v1-ontology-recall-reframe` — v1 재프레임(설계위험→온톨로지+회상), VISION §다음단계#1 supersede + 실현된 정적 스키마·기술 결정(tree-sitter-java·Neo4j·`edge_kind` 구성강제·조합형 회상). (active) → [`adr/ADR-20260701-v1-ontology-recall-reframe.md`](adr/ADR-20260701-v1-ontology-recall-reframe.md)
- ✅ `ADR-20260701-semantic-layer-load-contract` — 의미층 적재 계약: provider-free(LLM 0), 외부 요약을 근거결박·`edge_kind='inferred'` 분리·provenance 강제로 적재, 회상 'summaries' 분리 채널. 내용검증·durability·대상확장·자동재생성은 유예/범위 밖(change-condition 명시). (active) → [`adr/ADR-20260701-semantic-layer-load-contract.md`](adr/ADR-20260701-semantic-layer-load-contract.md)
- ✅ `ADR-20260702-community-deterministic-structural` — Community 멤버십 = 결정론적 구조 분할(Class 수준 연결 요소, `MEMBER_OF` deterministic, LLM·GDS 없음, `recall_community` 진입점). ADR-20260701-v1을 구체화(supersede 아님) — 유예된 것은 생성형 `CommunityReport` prose이고 그것은 계속 유예. (active) → [`adr/ADR-20260702-community-deterministic-structural.md`](adr/ADR-20260702-community-deterministic-structural.md)
- 🔶 `ADR-20260702-communityreport-load-contract` — CommunityReport 적재 **계약 초안**(proposed): `ADR-20260701` 적재 계약을 target=`Community`로 정련(Summary wire shape·`SUMMARIZES`·'summaries' 채널 재사용, grounding=멤버 Class로 강화, `code_bound_at`=Community `committed_at`). `ADR-20260702-community-deterministic-structural` line36(CommunityReport→ADR-20260701 라우팅) 이행. **로더·노드 구현은 외부 생산자 확정까지 유예**(→active 조건은 change_condition). → [`adr/ADR-20260702-communityreport-load-contract.md`](adr/ADR-20260702-communityreport-load-contract.md)
- 🔶 `ADR-20260702-risk-designdecision-load-contract` — Risk·DesignDecision 적재 **계약 초안**(proposed): `ADR-20260701`을 1급 inferred 엔티티로 일반화 — 새 노드 라벨 `Risk`/`DesignDecision`(namespace 격리 `risk:`/`decision:` id), inferred 엣지 `DECIDES`/`SUPERSEDES`/`RISKS`/`ADDRESSES_RISK`(`edge_kind='inferred'`), grounded 노드 강제(근거 0개 판정 금지), 전용 로더(generic deterministic ingest 재사용 금지). `DESIGN.md` §2 온톨로지 구체화. **로더·노드·엣지 구현은 외부 생산자 확정까지 유예**(→active 조건은 change_condition). → [`adr/ADR-20260702-risk-designdecision-load-contract.md`](adr/ADR-20260702-risk-designdecision-load-contract.md)
- 🔶 **ADR 후보(결정 굳으면 승격):** 신선도 2축 중 결정-계보(`valid_from·valid_to`) · 내용(semantic) 검증 방식·precision 가드레일(유예 #1) · 요약 durability git-SoT 계약(유예 #2) · 노출 형태(MCP/스킬).

---

*이 문서의 ✅는 ADR/코드/intent를 가리키는 포인터다. 🔶·⬜가 결정으로 굳으면 ADR을 쓰고 여기 태그를 올린다 — 그래야 이 계획 지도가 drift 없이 살아있다. 계획의 SoT는 여기, 사실·동작·결정의 SoT는 코드·ADR·intent.*
