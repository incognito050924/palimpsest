# palimpsest — 시스템 설계 지도 (v0)

> **위상:** 살아있는 설계 지도다. 얼린 스펙이 아니다. **권위 있는 결정은 ADR**(`.ditto/knowledge/adr/`)에 있고, 이 문서의 ✅는 그 포인터다. 🔶는 제안(미확정), ⬜는 미결(검증·결정 대기). 결정이 굳으면 ADR로 승격하고 여기 태그를 갱신한다.
> **목적:** 슬라이스들이 흩어지지 않게 정렬하는 골격. 전체 목표·온톨로지·아키텍처·기능·로드맵을 한 화면에 둔다.
> **소비자:** palimpsest 개발자/에이전트. **갱신:** 결정·스파이크마다. **폐기 조건:** 내용이 ADR·코드·계약으로 흡수되어 더 가리킬 게 없어지면.
> work item: `wi_260626v8v`. 작성: 2026-06-26 (v0).

**범례:** ✅ 확정(ADR/intent 근거) · 🔶 추정(제안, 미확정) · ⬜ 미결(검증/결정 대기)

---

## 1. 최종 목표 / 비전

- ✅ **정체성:** palimpsest는 한 프로젝트/프로덕트의 라이프사이클 전체 — 의도·결정·코드·관계·위험 — 을 담는 **Knowledge Graph 기반 장기기억·지식 큐레이터**다. "의사결정 메모리"는 그 일부일 뿐. (ADR-20260626, VISION)
- ✅ **세 목표:** ① LLM·에이전트 **할루시네이션 최소화**(근거 결박) · ② **context rot 없는 점진 회상**(필요분만, 토큰 1급 제약) · ③ **의사결정·의도 투명화**(숨은 의도 충돌 방지). (VISION)
- ✅ **독립성:** palimpsest는 **standalone**이다. ditto 위에 얹히지 않는다. 개발 단계엔 ditto가 *개발 도구*로 쓰이고(설계에 비침투), 완성 후엔 ditto가 *소비자*가 되어 palimpsest를 호출한다. ditto는 **노출 경계에서만** 고려한다. (ADR-20260626 #1, 잠긴 결정 #4/#5)

## 2. 도메인 모델 / 온톨로지 *(가장 중요 · 가장 미결)*

**원칙 (✅):** KG가 본체. **모든 엔티티가 1급**(단일 중심 단위 없음). **전 이력 보존** — 채택뿐 아니라 버려진 대안·중단된 접근·이전 결정까지(회귀 방지). **provenance(출처)와 신선도가 1급 속성**. (ADR-20260626)

- 🔶 **엔티티 타입(제안, v1 최소 + 확장):** `Commit` · `File` · `Symbol`(Function/Class/Module) · `Change`(diff 단위) · `Branch` · `Author`(사람/에이전트) · `DesignDecision`(↔ADR) · `Requirement` · (확장) `Conversation`/`AgentTrace` · `Community`(GraphRAG 요약 노드).
- 🔶 **관계 타입(제안):** `MODIFIES` · `CALLS`/`DEPENDS_ON` · `IMPLEMENTS` · `DECIDES`/`SUPERSEDES` · `AUTHORED_BY` · `RISKS`/`CONFLICTS_WITH` · `EVOLVED_FROM`(이력 계보).
- 🔶 **provenance:** 모든 노드·엣지에 `source`(commit SHA / 문서 / 대화) + `confidence` + `gap`(모르는 것). 생성형 추론은 반드시 이 셋으로 사실과 분리(세탁 금지).
- 🔶 **신선도(2축, 제안):** ① **코드-결박 신선도** — 지식이 가리키는 코드 위치·심볼이 현재 커밋에 살아있나 · ② **결정-계보 신선도** — 이 지식이 더 최신 결정에 의해 supersede 됐나(이력은 보존하되 "현재 live" 판정). VISION의 "커밋 해시 기반 2축 신선도"를 이렇게 구체화 제안.
- ⬜ **미결:** 정확한 2축 정의·계산 · 기억의 입도(granularity) · 임베딩 부착 대상과 차원 · 온톨로지 진화/버전 전략 · `Risk`를 노드로 둘지 관계로 둘지.

### 2-bis. node/edge 스키마 제안 *(연구 반영, 2026-06-30 · 🔶 medium confidence)*

1차 자료 종합 — Glean(pre-compute fact)·HugRAG(unified edge space)·CPG/Joern(type-label overlay)·Graphiti(bi-temporal/Episode). 근거·검증(23 confirmed/2 refuted)·출처 전체: [`docs/research/precompute-hugrag-kg.md`](research/precompute-hugrag-kg.md). **충돌 검토: ADR-20260626(KG 본체·GraphRAG 회상·전이력 보존)과 충돌 없음** — 오히려 각 시스템이 우리 모델 조각을 실증.

- 🔶 **노드 — 정적층(CPG):** `Repo` · `Module/File` · `Type/Class` · `Method/Function` · `Variable/Local` · `CallSite`. **의미층:** `Summary/CommunityReport` · `DesignDecision/ADR` · `Risk/Finding` · `Episode/SourceCommit`.
- 🔶 **엣지 — 결정론적 구조(`edge_kind=deterministic`, git projection 재생성):** `CONTAINS` · `CALLS` · `REACHING_DEF`(DATAFLOW) · `IMPORTS` · `INHERITS` · `REF`. **생성형 추론(`edge_kind=inferred`, LLM 생성 + confidence 게이팅):** `SUMMARIZES` · `CAUSALLY_RELATES` · `ADDRESSES_RISK` · `DECIDES` · `RELATES_TO`.
- 🔶 **정적/생성형 분리(세탁 금지):** ① 별도 edge label + ② 모든 엣지에 `edge_kind = deterministic|inferred` 속성 — 2중 표시로 출처·신뢰 혼동 차단.
- 🔶 **provenance·2축 신선도 = 엣지 속성(Graphiti 패턴):** `source`=Episode/commit SHA(provenance) · `valid_from`/`valid_to`=결정-계보 신선도(삭제 대신 invalidate=전이력 보존) · `code_bound_at`=코드-결박 신선도(연결 심볼의 마지막 git 변경) · inferred일 때 `generator`/`model`/`confidence`/`created_at`.
- ⚠ **제안 한계:** 노드/엣지·속성 *이름*은 제안이며 **v1 design-risk 스파이크로 검증 후 ADR 승격**. HugRAG의 "세 엣지 = 정적/생성형 1:1"은 검증서 기각(실제 2:1) — 아이디어만 차용하고 매핑은 직접 설계.

## 3. 아키텍처

- ✅ **git = SoT.** KG는 재구축 가능한 projection, 벡터는 보완. (ADR-20260626 #2, 잠긴 결정 #2)
- 🔶 **컴포넌트(제안):**
  - **Capture** — git(커밋·diff·브랜치) → 추출 → 중간표현(IR). (확장: 문서·대화·에이전트 트레이스). 자동 기본 + 실시간/배치/명시 혼합.
  - **KG Store** — 그래프 DB(🔶 Neo4j 1차) + 네이티브 벡터 인덱스. provenance·신선도 부착.
  - **Recall/Curate** — GraphRAG: 그래프 탐색 + 벡터 + LLM 합성. 조합형(계보·여정) + 생성형(근거 결박).
  - **Exposure Adapter** — 얇은 층. 코어를 노출 메커니즘에서 분리.
- 🔶 **데이터 흐름:** `git → Capture → IR → KG build/update(provenance·신선도) → Recall(query) → Curate(synthesize, grounded) → Adapter → 소비자`.
- ✅ **노출 독립:** 코어(KG·GraphRAG)는 노출 메커니즘과 무관. 노출은 갈아끼우는 어댑터. ditto 내부 구조에 비의존.

## 4. 기능 명세 (5기능)

각 기능 = 입력 / 동작 / 출력 / 근거결박. 기능 정체성은 ✅(VISION), 상세 동작은 🔶/⬜.

| 기능 | 입력 | 동작 | 출력 | 비고 |
|---|---|---|---|---|
| **Preserve** ✅ | git 변경 | KG 노드·엣지 생성/갱신 + provenance·신선도 | 보존된 KG 조각 | 전 이력 보존 |
| **Relate** ✅ | 엔티티들 | 코드·결정·의도·여정 간 관계 투영 | 질의 가능한 그래프 | 벡터 보완 |
| **Recall** ✅ | 작업 맥락 질의 | 필요분만 점진 회상(🔶 pull 우선, push 확장) | 관련 조각(토큰 예산 내) | context rot 회피 |
| **Curate** ✅ | 회상된 자료 | 조합형(계보·여정 구성) + 생성형(LLM 합성) | 답 + **출처+gap+confidence** | 세탁 금지 |
| **Reconcile** ✅ | 브랜치 간 컨텍스트(개인↔팀) | 차이 인정 → 신선도·우선순위 판정 | 무엇이 더 신선/우선인지 | v1 design-risk가 그 씨앗 |

⬜ 각 기능의 상세 동작·계약은 슬라이스별로 구체화.

## 5. 노출 / 통합

- ⬜ **메커니즘 미결:** MCP 서버 / 스킬 등록 / ditto-pluggable. Python 주언어는 셋 다 가능(MCP Python SDK · 언어 무관 스킬). (VISION §다음단계 #5)
- ✅ **소비자 일반화:** 개발자(사람) + 에이전트. ditto는 **첫 소비자일 뿐**, 노출을 ditto에 특화하지 않는다.
- 🔶 **회상 계약(제안):** `query(작업 맥락) → grounded answer(출처+gap+confidence) + 점진 확장 핸들`. 사람용(설명형)·에이전트용(토큰 맞춘 근거 조각) 양면.

## 6. 로드맵 / 슬라이스 계획

- ✅ **v1 = design-risk slice** (`wi_2606263sn` intent): 최근/진행 브랜치 변경을 캡처→KG→GraphRAG 회상해 "팀원의 최근 push가 내 현재 설계에 위험인가"를 **근거 붙여 surface**. 성공 = **구조·동작 보장**, 퀄리티(정확도·헛경보)는 다음. pull 우선.
- 🔶 **이후 슬라이스(확장축)** — 각 슬라이스가 켜는 기능·온톨로지 조각:

  | 슬라이스 | 켜는 것 |
  |---|---|
  | 위험판정 **퀄리티** | Curate 정밀도, recall@k, 헛경보 억제 |
  | **push** 능동 경고 | Recall push 트리거·개입시점 |
  | **페르소나** 회상 | 소비자별 뷰(PM/아키텍트/…) |
  | 전체 **backfill** | Capture 소급 발굴(전 git 이력) |
  | **에이전트 트레이스** 캡처 | `Conversation`/`AgentTrace` 엔티티 |
  | **전체 온톨로지** | §2 엔티티·관계 전체 |
  | **Reconcile** 본격 | 브랜치 간 신선도 중재 |
  | 조직·**cross-repo** | 다중 저장소 스코프 |

## 7. 미결 · 검증 대상

- ⬜ **DB substrate** — 🔶 Neo4j Community 1차 + Memgraph 폴백(문서 근거), Postgres+AGE 제외. **Neo4j 도입 방향 확정**(스파이크 권고와 일치), 단 성능·메모리·재구축 **실측 미결**(스키마+코퍼스 후 마이크로벤치)이라 ADR 승격은 실측 후. → `docs/spikes/db-substrate-spike.md` (`wi_2606264gw`).
- ⬜ **node/edge 설계 미결(연구 §6)** — derived/inferred 엣지 생성 메커니즘(Datalog식 파생 vs Cypher+앱코드) · LLM 추론 엣지 precision 가드레일(거짓 인과 폭증 억제) · CPG intra-procedural data-flow를 cross-branch로 확장 · 코드-결박 신선도 갱신 단위·stale 판정 트리거. → `docs/research/precompute-hugrag-kg.md` (`§2-bis`).
- ⬜ **스키마 상세** — §2 엔티티/관계/신선도 2축의 구체 정의.
- ⬜ **임베딩 설계** — 부착 대상·차원·하이브리드 검색 구성.
- ⬜ **데모 코퍼스** — 두 브랜치에 실제 설계 의존성 있는 repo(ditto / 최소 fixture / 타 프로젝트).
- ⬜ **성능** — ingest·순회·벡터검색·재구축·메모리(스파이크 §4 지표).
- ⬜ **노출 형태** — MCP/스킬/pluggable 택일.

## 8. ADR 인덱스 (권위)

- ✅ `ADR-20260626-foundational-architecture` — KG 본체 + GraphRAG 회상층, 전 이력 보존, 자동 캡처. (active)
- 🔶 **ADR 후보(결정 굳으면 승격):** DB substrate 택일(Neo4j — 실측 후) · 온톨로지/스키마(§2-bis node/edge·`edge_kind` 정적/생성형 분리 — v1 스파이크 후) · 신선도 2축 정의(`code_bound_at`/`valid_from·valid_to`) · 노출 형태.

---

*이 문서의 ✅는 ADR/intent를 가리키는 포인터다. 🔶·⬜가 결정으로 굳으면 ADR을 쓰고 여기 태그를 올린다 — 그래야 이 지도가 drift 없이 살아있다.*
