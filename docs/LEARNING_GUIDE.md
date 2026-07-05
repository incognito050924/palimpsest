# palimpsest 코드 학습 가이드

> 이 문서는 palimpsest 소스를 **한 파일씩 직접 읽으며 이해하려는 사람**을 위한 동반 지도다.
> 코드 읽기를 대체하지 않는다 — 어느 순서로, 어느 심볼부터, 무엇을 주의하며 읽을지를 안내하고
> 모든 항목은 `파일:라인`으로 실제 코드를 가리킨다. 권위는 코드에 있다(문서가 코드와 어긋나면 코드가 맞다).

작성 시점 기준: 소스 4,301줄(18파일) + 테스트 5,539줄. 전체 스위트 189 passed.

---

## 0. 5분 멘탈 모델 (먼저 이것부터)

palimpsest의 최종 형상은 두 축이다 (ADR-20260626 정초 아키텍처):

1. **Knowledge Graph 본체** — 코드베이스를 그래프로 적재한다.
2. **GraphRAG 회상층** — 그 그래프에서 근거 있는 답을 회상한다.

관통하는 대원칙 **네 가지**를 먼저 머리에 넣으면 나머지가 전부 이 변주로 읽힌다:

| 원칙 | 뜻 | 어디서 체감되나 |
|---|---|---|
| **provider-free** | palimpsest는 LLM을 **절대 호출하지 않는다**. 요약·위험·결정 같은 "판단"은 외부가 만들어 건네고, palimpsest는 **적재/회상만** 한다. | `kg/summary.py:1`, `recall/graphrag.py:1-42` |
| **git = SoT** | git이 진실의 원천. Neo4j는 언제든 drop 후 재구축 가능한 **파생 투영**. 그래서 모든 쓰기가 멱등(MERGE-on-id). | `cli.py:20-29`, `backfill.py:13` |
| **결정론 vs 추론 분리 (no-laundering)** | 코드에서 직접 파생한 구조 엣지(`edge_kind=deterministic`)와 외부가 판단한 의미 엣지(`edge_kind=inferred`)를 **절대 섞지 않는다**. | `ir.py:102-106` |
| **근거 결박 (grounding)** | 모든 추론 엔티티·회상 항목은 실제 그래프 노드/커밋에 묶여야 한다. 안 풀리면 통째로 거부하거나 GAP로 표기한다. | 로더의 atomic-reject, 회상의 `gaps` |

데이터 흐름 한 줄 요약:

```
소스(.java) ─┐
             ├─► extract ─► IR(중간표현) ─► ingest ─► Neo4j 그래프 ─► recall(GraphRAG) ─► 근거 달린 답
git 이력 ────┘                                 ▲
외부 판단 payload(JSON) ───► load ─────────────┘
```

---

## 1. 권장 학습 경로 (전체 코드베이스 순서)

파일을 무작정 여는 대신 **데이터가 흐르는 순서 = 개념이 쌓이는 순서**로 읽어라. 아래 6단계가 그 순서다.
각 단계는 이 문서의 같은 번호 섹션에 대응한다.

| 순서 | 계층 | 파일 | 왜 이 순서 |
|---|---|---|---|
| **1** | 데이터 모델(IR) | `ir.py` + 패키지 `__init__` | 모든 계층이 쓰는 **어휘**. 데이터 모양을 알아야 나머지가 읽힌다. |
| **2** | 추출(extract) | `extract/java.py`, `extract/provenance.py` | 소스/git → IR. 그래프 재료가 어디서 오는지. |
| **3** | 구조 적재(ingest) | `kg/ingest.py` | IR → Neo4j. MERGE 멱등, Cypher 안전, **Episode 함정**. |
| **4** | 의미층 로더 | `kg/relation.py → risk.py → community.py → decision.py → summary.py` | 구조 위에 얹는 외부 판단. `relation.py`가 가장 단순한 대표 예시. |
| **5** | 회상(GraphRAG) | `recall/graphrag.py` | 가장 큰 모듈(1,096줄). 채널별로 접근. |
| **6** | 이력·브랜치·진입점 | `cli.py → backfill.py → reconcile.py` | 전체를 묶는 실행 표면. CLI로 진입점 지도부터. |

읽기 팁:
- 각 단계에서 **테스트를 함께 열어라**. 이 프로젝트는 TDD로 만들어져 테스트가 "이 함수가 무엇을 보장하는가"의 실행 가능한 명세다.
- 단계 3의 **Episode 무음 드롭 함정**과 단계 5의 **DEFAULT_RELATIONS 화이트리스트**는 설계의 급소다. 거기서 멈춰 충분히 이해하고 넘어가라.

---

## 2. 데이터 모델 — `ir.py` (513줄)

**책임**: 직렬화 가능한 도메인 모델(IR = 중간표현). extract가 채우고 ingest가 소비한다.

### 핵심 타입
| 심볼 | ir.py:라인 | 설명 |
|---|---|---|
| `branch_scoped_id(branch, qn)` | 32–43 | **순수 identity 함수**. 노드 id와 엣지 끝점이 이 함수를 공유해야 일관성 보장 |
| Node kind 상수 | 46–53 | `REPO, PACKAGE, FILE, CLASS, METHOD, COMMUNITY` |
| 결정론 엣지 상수 | 55–68 | `CONTAINS, IMPORTS, CALLS, DEPENDS_ON, MEMBER_OF, MODIFIES` |
| 추론 계층 상수 | 73–99 | `SUMMARY/SUMMARIZES`, `RISK/RISKS`, `DESIGN_DECISION/DECIDES/SUPERSEDES/ADDRESSES_RISK`, `CAUSALLY_RELATES/RELATES_TO/CONFLICTS_WITH` |
| `EDGE_KIND_DETERMINISTIC` / `_INFERRED` | 105–106 | 두 계층을 가르는 no-laundering 마커 |
| `EMBEDDING_DIM = 1536` | 114 | 임베딩 차원 단일 상수(벡터 인덱스와 공유) |
| `Provenance` | 117–130 | git 근거: `source_commit, author, committed_at` |
| `Node` | 133–163 | 구조 엔티티. `.id`(150–152) = `branch_scoped_id(branch, qualified_name)` |
| `Edge` | 166–186 | 방향 관계. `dst`는 미해결 외부 qn 허용(IMPORTS) |
| `IR` | 189–219 | 추출 결과 컨테이너 + 조회 헬퍼 |
| `scope_to_branch(ir, branch)` | 222–241 | 순수 transform: identity를 브랜치로 접은 **새** IR(원본 불변) |
| 추론 dataclass | 244–513 | `SummaryClaim, Summary, Risk, InferredRelation, DesignDecision` |

### 읽기 순서
docstring(1–19) → 상수(45–114) → `Provenance`(117) → `Node`(133) → `Edge`(166) → `IR`(189) → `branch_scoped_id`(32)+`scope_to_branch`(222) → 추론 dataclass는 `Summary`(268) 하나만 정독하면 나머지는 같은 패턴.

### 꼭 기억할 불변식
- **branch-scoped id**: `branch=None`이면 bare qn(단일 브랜치와 byte-identical, 가산적). named면 `branch:<b>\x1f<qn>`. 노드와 엣지 끝점이 **같은 함수**를 써야 한다.
- **code_bound_at은 IR 필드가 아니다** — freshness는 생성기 wall-clock이 아니라 결박된 코드 노드의 `committed_at`을 따라야 하므로 로더가 계산한다.
- **grounding은 entity-atomic** — 추론 엔티티의 참조가 하나라도 안 풀리면 그 엔티티 전체를 거부(세탁 방지).
- **`semantic_verdict`(외부 judge)와 `confidence`(생성기)는 별개 필드** — 혼용 금지.

### 공개 API 표면 (패키지 `__init__`)
- `palimpsest/__init__.py` — 버전만. `__main__.py` → `cli.main`.
- `extract/__init__.py` — `extract, read_provenance, changed_paths`.
- `kg/__init__.py` — `create_constraints, ingest, ingest_modifies` + 각 `load_*`/`*_id`.
- `recall/__init__.py` — 9개 회상 진입점(아래 §5).

검증: 전용 `test_ir.py`는 없다. `tests/reconcile/test_scope_to_branch.py`(identity 규칙), `tests/extract/test_extract.py`(생성 경로), `tests/kg/test_*.py`(각 dataclass)가 간접 검증.

---

## 3. 추출 — `extract/` (401줄)

**책임**: 소스/git → IR. `java.py`는 tree-sitter-java로 CPG(Code Property Graph) 골격을, `provenance.py`는 git 메타데이터를 뽑는다. **best-effort** — 타입 완전해석·Lombok 생성 멤버는 없다(`java.py:1-6`).

### `java.py` (324줄) — 무엇을 만드나
- **노드**: REPO / PACKAGE / FILE / CLASS / METHOD
- **엣지**: `CONTAINS`(계층), `IMPORTS`(File→대상), `DEPENDS_ON`(Class→Class, 필드·파라미터·import 타입), `CALLS`(Method→Method, 이름 기반)

| 함수 | 라인 | 역할 |
|---|---|---|
| `extract` | 273–324 | 진입점. `.java` 루프 → walker → 전역 엣지 → Repo/Package 조립 |
| `_FileWalker.run` | 103–126 | 파일 1개 순회, FILE 노드 + Package→File CONTAINS |
| `_FileWalker._type_decl` | 141–180 | 클래스/인터페이스/enum/record(재귀), CLASS 노드 |
| `_FileWalker._method_decl` | 182–206 | 메서드/생성자, METHOD 노드(fqn에 파라미터 시그니처 포함) |
| `_collect_call_names` | 209–220 | body DFS로 `method_invocation` 이름 수집 |
| `_depends_on_edges` / `_calls_edges` | 231 / 247 | 단순명 매칭으로 DEPENDS_ON / CALLS 생성(self-loop 억제) |

핵심: tree-sitter **쿼리 DSL을 안 쓰고** 전부 수동 순회(`named_children` + `child_by_field_name`).

### `provenance.py` (77줄)
| 함수 | 라인 | 뽑는 것 | git 호출 |
|---|---|---|---|
| `read_provenance` | 11–31 | `source_commit`(%H), `author`(`%an <%ae>`), `committed_at`(%cI) | `git show -s --format=...` 한 번(`\x1f` 구분) |
| `changed_paths` | 34–77 | 커밋이 건드린 repo-상대 경로 | `git diff-tree --root --no-commit-id --no-renames --first-parent -r -z --name-status` |

### 읽기 순서
`ir.py` 상수/타입 → `java.py`의 `extract`(273) → `_FileWalker.run`(103)→`_type_decl`(141)→`_method_decl`(182) → 해석 `_depends_on_edges`/`_calls_edges` → 마지막 헬퍼 → `provenance.py`(짧고 독립적).

### 주의점
- **git diff-tree 동작**: `--root`로 최초 커밋은 빈 트리와 diff해 전체 파일 보고(안 그러면 조용한 누락). `--first-parent`에서 **머지 커밋은 빈 diff → MODIFIES 0개 기여**(의도된 것 — 개별 변경은 각 커밋에 이미 결박, 재결박하면 churn 이중집계). evil-merge 고유 내용은 **수용된 gap**.
- **author는 속성일 뿐** — 별도 Author 노드/엣지를 만들지 않는다(§7 폐기 참조).
- **fail-loud 파싱** — `changed_paths`는 토큰 짝이 안 맞으면 즉시 `ValueError`(경로 오정렬보다 실패를 택함).
- **CALLS/DEPENDS_ON은 단순명 매칭** — 동명 심볼 오버매칭 가능(best-effort의 한계).

검증: `tests/extract/test_extract.py`, `tests/extract/test_changed_paths.py`.

---

## 4. 구조 적재 — `kg/ingest.py` (291줄)

**책임**: IR 노드/엣지를 Neo4j에 `UNWIND + MERGE`로 배치 적재. 라벨당 `id` UNIQUE 제약이 있어 모든 쓰기가 **MERGE-on-id**(멱등).

| 심볼 | 라인 | 역할 |
|---|---|---|
| `ingest` | 248 | 한 트랜잭션: Episode → 노드(라벨별) → 엣지(rel별) MERGE |
| `create_constraints` | 196 | 라벨별 `id` UNIQUE 제약(멱등) |
| `ingest_modifies` / `_MODIFIES_MERGE` | 136 / 123 | **MODIFIES 전용 writer** (Episode·File 둘 다 MATCH) |
| `_episode_rows` | 236 | provenance에서 커밋별 Episode 행 **합성** |
| `_NODE_MERGE` / `_REL_MERGE` / `_EPISODE_MERGE` | 70 / 92 / 104 | 쿼리 3종 |
| `id_to_label` 필터 | 264–271 | 엣지 끝점 라벨 해석; 미해석이면 skip |
| `wipe_branch_plane` / `reap_dead_branches` | 174 / 186 | 브랜치 평면 GC |

### ★ 급소: Episode 무음 드롭 함정 (반드시 이해)
- Episode는 **IR 노드가 아니다**. `_episode_rows`(236)가 provenance에서 커밋 SHA 단위로 **합성**한다 → `ir.nodes` 밖.
- 그래서 `ingest`의 `id_to_label` 맵(264)에 Episode의 id(커밋 SHA)가 **없다**.
- MODIFIES를 제네릭 엣지 경로로 태우면 `id_to_label.get(e.src)`가 `None` → 269–270에서 **조용히 드롭**. 모든 MODIFIES가 소리 없이 사라진다.
- 그래서 `ingest_modifies` + `_MODIFIES_MERGE` **전용 writer**가 필요 — 끝점을 `Episode`/`File` 라벨로 직접 MATCH.

> 이건 이 코드베이스의 대표적 교훈이다: **provenance 노드(ir.nodes 밖)로 향하는 엣지는 제네릭 라이터로 태우면 무음 드롭된다 → 전용 writer 필수.**

### Cypher 안전 패턴
데이터는 항상 `$params` 바인딩(rows/edge_kind/branch/live), **라벨·rel type만** 닫힌 온톨로지(`NODE_LABELS`/`REL_TYPES`)에서 나온 값으로 `.format()`. 데이터에서 온 문자열은 절대 쿼리 텍스트에 안 들어간다(주입 불가).

### 읽기 순서
`ingest`(248) → `_node_row`/`_edge_row`/`_episode_rows` → 쿼리 3종 → `create_constraints` → `ingest_modifies`(+왜 전용인지) → GC 계열.

### 불변식
- MERGE 멱등(재적재 수렴). File은 HEAD-MERGE(**커밋별 버전드 노드 없음**) — 삭제 경로는 phantom File 없이 skip.
- 엣지 끝점은 라벨 지정 MATCH(labelless는 AllNodesScan). branch GC는 `branch` 속성 기준, **bare 평면(branch=null)은 절대 reap 안 함**.
- `edge_kind="deterministic"` 강제, 추론 엣지 타입은 `REL_TYPES`에서 의도적 제외.

검증: `tests/kg/test_ingest.py`(멱등·edge_kind 분할·인덱스 MATCH), `tests/kg/test_ingest_modifies.py`(deterministic·멱등·phantom 없음·author 부재).

---

## 5. 의미층 로더 — `kg/{relation,risk,community,decision,summary}.py` (972줄)

**책임**: 정적 CPG 위에 **외부가 만든 판단**을 노드/엣지로 결박. palimpsest는 판단하지 않고 적재만 한다(provider-free). 만드는 엣지는 전부 `edge_kind=inferred`.

> 왜 결정론 writer(`_REL_MERGE`)를 재사용하지 않나? 그걸 쓰면 (1) `edge_kind=deterministic`으로 **세탁**되고 (2) 미해결 끝점에서 조용히 no-op 된다. 그래서 각 로더는 끝점을 **선(先)해결**하고, 안 풀리면 **엔티티 전체를 거부**한다.

| 파일 | 핵심 함수 | 만드는 것 | 특징 |
|---|---|---|---|
| `relation.py` (166) | `load_relations` | **노드 없이** 순수 inferred 엣지 | 가장 단순 — 대표 예시 |
| `risk.py` (204) | `load_risks` | `Risk` + `RISKS` | `flags`(코드노드)로 grounding, code_bound_at=flags[0] |
| `community.py` (131) | `compute_communities`, `augment_communities` | `Community` + `MEMBER_OF`(**deterministic**) | union-find로 Class 배타·평면 분할, rebuild-stable |
| `decision.py` (287) | `load_design_decisions` | `DesignDecision` + `DECIDES/SUPERSEDES/ADDRESSES_RISK` | 라벨 검증 + **bi-temporal**(valid_from/valid_to) |
| `summary.py` (384) | `load_summaries`, `create_vector_index` | `Summary` + `SUMMARIZES` | 다중 claim grounding + CommunityReport 멤버십 + **임베딩/벡터인덱스** |

주의: `community.py`만 예외로 **결정론 구조 분할**(inferred 아님)이라 일반 ingest writer를 재사용해 `deterministic`으로 적재된다.

### 공통 적재 계약 (다섯 모듈이 공유하는 관용구)
1. **근거결박 + atomic-reject**: 끝점 `_resolve` 선해결, 미해결 하나라도 있으면 엔티티 전체 거부(`*Rejection`으로 이유 표면화, 나머지는 계속).
2. **inferred 분리**: 모든 엣지 `edge_kind=inferred`, id는 네임스페이스 프리픽스(`summary:`/`risk:`/`decision:`/`community:`)로 코드 qn과 충돌 불가.
3. **MERGE 멱등**: id는 정렬된 키의 결정론적 해시(rebuild-stable).
4. **결정론 writer 미재사용**: baked-label 쿼리 + `$params`(주입 방지).
5. **freshness = 코드 추종**: `code_bound_at`은 결박된 코드 노드의 `committed_at`에 앵커.

### 읽기 순서 (단순 → 복잡)
`relation.py`(관용구를 가장 순수하게) → `risk.py`(노드+flag grounding) → `community.py`(결정론 대조군, union-find) → `decision.py`(bi-temporal supersede) → `summary.py`(전부 + 멤버십 + 임베딩).

### 눈여겨볼 불변식
- **멤버십-grounding**(summary): CommunityReport(target `community:`)의 claim ref는 그 community 멤버여야 한다 — 임의 코드로 근거 세탁 불가.
- **결정론적 community**: union-find root를 사전순 min에 부착 + 결과 정렬 → 순서·rebuild 무관하게 동일 파티션. Class는 정확히 1개 community.
- **bi-temporal**(decision): supersede는 삭제가 아니라 `valid_to` SET(전이력 보존). live = `valid_to IS NULL`.
- **임베딩**(summary): 차원 = `EMBEDDING_DIM`(인덱스와 동일 pin, 잘못된 차원은 Neo4j가 조용히 미색인하기 전에 거부), 인덱스당 단일 embedding_model.
- **알려진 단순화**(코드 주석 명시): risk/decision/relation의 code_bound_at은 단일 앵커 — 다중 타깃 per-edge 신선도는 미구현(ADR change_condition).

검증: `tests/kg/test_{relation,risk,community,decision,summary,community_report}.py`.

---

## 6. 회상 — `recall/graphrag.py` (1,096줄, 가장 큰 모듈)

**책임**: 그래프 위의 GraphRAG 회상. (a) 구조 순회 BFS + (b) 의미 KNN + (c) 근거 반환을 결합한다.
불변식(docstring 1–42): **조합적 조립만 — 이 경로에 LLM 없음.** 출력 필드는 병합되지 않고 항상 분리:
`{items, sources, summaries, gaps, confidence, expand_handle, risks, decisions, relations}`.

### 회상 채널
| 채널 | 라인 | 무엇을 회상 |
|---|---|---|
| **forward** `recall` / `_hop` / `expand` | 501 / 445 / 698 | seed에서 CALLS/DEPENDS_ON/CONTAINS/IMPORTS를 depth·limit까지 무방향 BFS |
| **semantic KNN** `recall_semantic` | 871 | 쿼리 벡터의 top-k 코사인 Summary. `score`(코사인)와 `confidence`(근거 커버리지) **분리** |
| **summaries** `_summaries` | 239 | 회상 노드에 결박된 inferred Summary + 근거 span |
| **risks / decisions** `recall_risk` / `recall_decision` | 637 / 675 | 코드를 FLAGS/DECIDES하는 엔티티(엣지별 라벨) |
| **community** `recall_community` | 588 | Community 멤버 Class(MEMBER_OF 역방향) |
| **relations** `_relations` | 410 | 회상 노드에 닿는 inferred 엣지 |
| **churn / co-change** `recall_churn` / `recall_cochange` | 1054 / 1073 | 커밋 수 hotspot / 동시변경 File(fanout cap 512) |
| **reconcile** `reconcile_recall` | 933 | 심볼의 브랜치별 peer N-way 비교, UTC instant 랭킹 |

### 공통 관용구
- **`_sources`(129)** — 항목마다 `{source_commit, path, start_line, end_line, committed_at}`만 투영. **author(%ae)는 선택 자체를 안 함** → 은닉이 투영 수준에서 보장.
- **bounded** — 모든 Cypher가 `ORDER BY <key> [DESC], id`(총순서 tiebreak) 후 서버측 `LIMIT`. 고차수 노드를 클라이언트로 스트리밍하지 않음.
- **`_result`(468)** — 모든 진입점의 단일 출력 조립기(필드 분리 강제).

### ★ 급소: DEFAULT_RELATIONS 화이트리스트 (63, 시행 185)
`items` 순회는 **오직 `CALLS/DEPENDS_ON/CONTAINS/IMPORTS`만**. 나머지는 의도적 제외:
- `SUMMARIZES/RISKS/DECIDES/...`(inferred) → 생성물이 조합적 근거에 섞이지 않게, 전용 채널로만.
- `MEMBER_OF` → Community 격리.
- **`MODIFIES` → author를 지닌 Episode를 일반 순회로 끌어들이지 않기 위해.** churn/cochange는 Episode를 **절대 RETURN 안 하고**(`count(DISTINCT e)`로만) File 끝점만 투영.

### 다른 불변식
- **author 절대 미투영**(모든 채널). **빈 결과 = 회상 GAP 표기**(확신에 찬 빈 답 금지, `gaps` 문자열).
- **branch 스코프** — semantic/reconcile/cochange가 브랜치 평면을 섞지 않음(bare Episode가 다리 놓지 못하게).
- **freshness는 detect-only** — target 재커밋 시 `stale=True`, 재생성은 안 함.
- 회상층은 온톨로지 상수의 **소비자**(ir.py에서 import)이지 정의자가 아니다.

### 1,096줄 읽기 순서
docstring(1–42) → 공통 헬퍼 `_kind`(121)/`_sources`(129)/`_item`(139)/`_confidence`(156) → `_neighbors`(177)/`_hop`(445)/`_result`(468)/`recall`(501)/`expand`(698) → `recall_semantic`(871) → inferred 채널(summaries→risks/decisions→relations, 같은 그룹핑 패턴) → 격리 진입점(community/risk/decision) → `reconcile_recall`(933) → churn/cochange(999–1096). **공통 헬퍼는 처음 한 번만 정독하면 나머지에서 반복 등장**.

검증: `tests/recall/`의 채널별 파일 전부(`test_recall.py`, `test_recall_semantic.py`, `test_traversal_bound.py`, `test_recall_churn.py`, `test_reconcile.py` 등).

---

## 7. 이력·브랜치·진입점 — `cli.py` / `backfill.py` / `reconcile.py` (715줄)

세 파일 모두 provider-free · 결정론 · 멱등.

### `cli.py` (380줄) — 사용자 진입점
argparse 서브커맨드로 전체를 배선. 출력은 **분리 섹션**(items/summaries/gaps/confidence), 병합 산문 금지.

| 서브커맨드 | 핸들러:라인 | 하는 일 |
|---|---|---|
| `ingest` | 72 | 단일 pinned 커밋 extract+ingest |
| `backfill` | 87 | 전 이력 replay |
| `query` | 109 | seed에서 bounded grounded recall |
| `reconcile` | 115 | N-way 브랜치 비교(exit code 사용) |
| `load` | 206 | 외부 summary JSON(파일 또는 **디렉터리**) 적재 |
| `churn` / `cochange` | 97 / 103 | hotspot / 동시변경 |

- `_driver`(60–69): Neo4j 연결을 **env**에서(`NEO4J_URI/USER/PASSWORD`).
- **"디렉터리 배치 = git-SoT rebuild"**(20–29): `load`에 디렉터리를 주면 정렬·batch 로드. 그 디렉터리가 git-tracked SoT. Neo4j drop 후 재실행으로 결정론적 재구축(멱등). load가 `create_vector_index`도 provision해 임베딩+인덱스가 drop을 견딤.

### `backfill.py` (118줄) — 전 이력을 그래프로
단일 커밋 ingest를 **모든 커밋에 oldest→newest 재생**. 각 커밋 트리를 `git archive`로 임시 디렉터리에 풀어(체크아웃 아님 → 워킹트리 불변) extract→ingest. freshness는 MERGE 무조건 SET이라 **최신 커밋이 이김**.

| 함수 | 라인 | 역할 |
|---|---|---|
| `backfill` | 77–118 | 메인 루프(커밋마다 materialize→extract→ingest→MODIFIES) |
| `_materialize_tree` | 62–74 | `git archive`→tarfile unpack(원본 불변) |
| `_commits_oldest_first` | 53–59 | `git log --reverse` |

핵심: `repo_name` 고정(87–92)으로 커밋마다 Repo 노드가 새로 생기는 것 방지. MODIFIES(103–113)는 bare 평면(`branch=None`)에 투영.

### `reconcile.py` (217줄) — branch-scoped identity
backfill을 **N개 브랜치로 일반화**. 같은 심볼의 발산 버전이 collapse 없이 공존. `capture`(161)가 메인: shallow 거부 → 브랜치 검증 → 평면 wipe → 합집합 커밋 추출 → 멤버십 fan-out → manifest.

**왜 브랜치가 정체성 차원인가**: 같은 qn이라도 브랜치가 다르면 다른 코드. `branch_scoped_id`가 브랜치를 MERGE 키에 접어 N개 버전을 별개 노드로 만든다 = 개인↔팀 컨텍스트 차이를 collapse 없이 표현.

불변식: **EXTRACT 축은 dedup, HISTORY 축은 절대 dedup 안 함**(전 브랜치 풀 히스토리 보존). scoped-rebuild(브랜치 평면 시작 시 1회 wipe, bare 평면 불건드림). shallow repo fail-closed. reconcile 랭킹은 UTC instant(tz-naive/파싱불가는 맨 뒤).

### 읽기 순서
`cli.py`(진입점 지도부터 역추적) → `backfill.py`(단일 라인 단순 케이스) → `reconcile.py`(N-브랜치 일반화, `_materialize_tree`를 backfill에서 import).

검증: `tests/backfill/`, `tests/reconcile/`, `tests/e2e/test_cli_e2e.py`, `tests/e2e/test_reconcile_e2e.py`.

---

## 8. 관통 불변식 요약 (한 곳에 모아서)

읽다가 "왜 이렇게?"가 들면 대개 아래 중 하나가 답이다.

| 불변식 | 근거 |
|---|---|
| **provider-free** — LLM 호출 0, 판단은 외부·적재만 | `summary.py:1`, `graphrag.py:1-42` |
| **git = SoT, Neo4j = 재구축 가능** — 전 쓰기 멱등(MERGE-on-id) | `cli.py:20-29`, `backfill.py:13` |
| **결정론 vs 추론 분리** — `edge_kind` 세탁 금지 | `ir.py:102-106`, 로더의 writer 미재사용 |
| **grounding / atomic-reject** — 미해결 참조는 엔티티 전체 거부 | 각 `load_*` |
| **author 은닉** — 회상 어디서도 `%ae` 미투영 | `graphrag.py:129`, `740-747` |
| **MODIFIES는 순회 whitelist 밖** — author-Episode를 일반 회상에 안 끌어들임 | `graphrag.py:63`, `999-1006` |
| **bounded 회상** — ORDER BY + tiebreak + 서버측 LIMIT | 전 채널 Cypher |
| **빈 결과 = GAP** — 확신에 찬 빈 답 금지 | 전 채널 `gaps` |
| **branch-scoped identity** — 브랜치가 노드 정체성 차원 | `ir.py:32-43`, `reconcile.py` |
| **freshness detect-only** — stale 감지, 재생성 안 함 | `graphrag.py:191-199` |
| **Episode 전용 writer** — provenance 노드 엣지는 제네릭 라이터로 무음 드롭 | `ingest.py:123-142` |

---

## 9. 손으로 확인하며 배우기

읽기만 하지 말고 실행해 보라. 테스트가 가장 빠른 피드백이다.

```bash
# 전체 스위트 (Docker Neo4j testcontainer 자동 기동)
.venv/bin/python -m pytest -q          # 189 passed 기대

# 한 계층만 집중해서
.venv/bin/python -m pytest tests/extract/ -q     # 추출
.venv/bin/python -m pytest tests/kg/ -q          # 적재 + 의미층
.venv/bin/python -m pytest tests/recall/ -q      # 회상
.venv/bin/python -m pytest tests/reconcile/ -q   # 브랜치

# 특정 동작 하나를 이름으로
.venv/bin/python -m pytest tests/kg/test_ingest_modifies.py -q -k idempotent
```

권장 실습 순서:
1. `tests/extract/test_extract.py`를 읽고 → `java.py`가 그 노드/엣지를 어떻게 만드는지 역추적.
2. `tests/kg/test_ingest_modifies.py`의 `test_deleted_path_makes_no_phantom_file` → HEAD-MERGE 불변식이 코드 어디서 지켜지는지 확인.
3. `tests/recall/test_recall_churn.py`의 author 미노출 테스트 → `graphrag.py`의 churn Cypher가 왜 Episode를 RETURN 안 하는지 확인.

---

## 10. 이 가이드가 다루지 않은 것 (경계)

- **아직 안 만든 것**: 하이브리드 회상(벡터→그래프 융합, 유예), AgentTrace/Conversation 캡처, MCP/스킬 노출, push 능동 경고, cross-repo. (설계 근거는 `.ditto/knowledge/DESIGN.md`와 `adr/`)
- **성능 실측 수치**: 벤치 하네스는 `bench/`에 있으나 절대 수치/합격선 판정은 이 가이드 범위 밖.
- 권위는 코드다. 이 문서와 코드가 어긋나면 코드를 믿고, 어긋난 지점을 알려 달라.
