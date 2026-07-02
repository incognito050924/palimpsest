# Spike — 그래프 DB 기반 선정: Neo4j vs Memgraph vs Postgres+pgvector+AGE

- 종류: 기술 스파이크(연구 리포트). work item: `wi_2606264gw`. 날짜: 2026-06-26.
- 목적: GraphRAG를 **단일 DB**(그래프 저장·순회 + 네이티브 벡터 인덱스 공존)에서 돌릴 첫 기반을 좁힌다.
- 상태: **문서·통합 성숙도 기반 1차 좁힘.** 성능·메모리·재구축 시간의 후보 간 우열은 **미검증**(스키마+코퍼스 확보 후 §4 마이크로벤치로만 확정).
- 관계: `ADR-20260626-foundational-architecture` §2(GraphRAG 회상층) 구현의 실측 입력. VISION.md §다음 단계 #4를 1차 좁힘 상태로 갱신.
- 권한: git=SoT라 DB는 교체 가능한 projection이므로 이 선택은 잠금이 아니라 **첫 빌드의 출발점**이다.

---

> **검증 방법**: 5개 각도(후보별 3 + GraphRAG 툴링 + 운영/성숙도)로 공식 문서·릴리스 노트·GitHub를 조사한 뒤, 권고가 가장 크게 의존하는 두 주장(Neo4j Community의 벡터 인덱스·GDS 커뮤니티 탐지 무료 여부)을 공식 문서에서 verbatim으로 독립 재확인했다.
> **표기**: `[Sx]` = 출처 마커. **[검증]** = 1차 문서 직접 확인. **[불확실]** = 추론 또는 단일·약한 근거.
> **결정적 사전 정정**: 흔히 "Neo4j의 벡터 인덱스와 GDS 커뮤니티 탐지는 Enterprise 전용"이라 알려졌으나 **사실이 아니다** — 둘 다 Community 무료(아래 표·검증). 이 오해가 결정을 좌우하므로 먼저 짚는다.

## 1. 비교표 (6개 기준 × 3개 후보)

| 기준 (가중) | **Neo4j** (Community/Enterprise) | **Memgraph** | **Postgres + pgvector + Apache AGE** |
|---|---|---|---|
| **① 단일-DB GraphRAG 적합도** *(최우선)* | 그래프 순회(Cypher) + 네이티브 벡터 인덱스(Lucene HNSW, 5.11+)가 한 DB에 공존, **Community에서도 동작**. `VectorCypherRetriever`가 벡터 시드→Cypher 순회를 한 번에 = GraphRAG recall 패턴 그대로. 통합 성숙도 최상 [S1][S6][S47] **[검증]** | 그래프(Cypher) + 네이티브 벡터 인덱스(USearch HNSW)가 한 DB에 공존, **GA**. 3.8(2026-02)에서 임베딩 단일저장으로 벡터 RAM ~85%↓. 통합 성숙도 양호·신생 [S20][S21][S22] **[검증]** | pgvector + AGE가 **한 DB에 공존은 가능**하나 "조립형": AGE 결과가 `agtype`라 벡터/관계형과 조인 시 캐스팅·`search_path` 설정 필요. 한 엔진이 아닌 두 확장의 결합 → 통합 성숙도 최하 [S37][S38] **[검증]** |
| **② GraphRAG 빌딩블록** (커뮤니티 탐지·provenance·신선도) | GDS에 Leiden·Louvain **무료 포함**(Community = "all algorithms", 4코어 제한). 프로퍼티 그래프로 provenance/freshness 자유 [S2][S3] **[검증]** | MAGE에 Leiden·Louvain **무료**. 프로퍼티 그래프 동일. 단 MAGE가 2026-01 본체 병합되며 Apache-2.0→BSL 이동 [S25][S26] **[검증]** | **네이티브 커뮤니티 탐지 없음** → Leiden은 외부(networkx/igraph)에서 계산. provenance/freshness는 컬럼·프로퍼티로 가능 [S34][S35] **[검증]** |
| **③ Python 생태계 & GraphRAG 툴링** | **가장 first-class**: 공식 `neo4j-graphrag`(1.18.0, 2026-06-24), `langchain-neo4j`, LlamaIndex `Neo4jPropertyGraphStore`, MS GraphRAG→Neo4j 임포트 [S5][S15][S14][S13] **[검증]** | **견고한 2위**: LlamaIndex `MemgraphPropertyGraphStore`(공식), LangChain `MemgraphGraph`(community), 자체 GraphRAG 문서. 전용 retriever 패키지는 없음 [S29][S30][S31] **[검증]** | **최약체**: LangChain `AGEGraph`(community)만. LlamaIndex property-graph store 없음(이슈만). 벤더 GraphRAG 레퍼런스 없음 [S39][S41] **[검증]** |
| **④ git=SoT 투영 적합도** (스핀업·재구축·운영 경량성, **낮을수록 좋음**) | 가장 무거운 베이스라인: JVM heap + page cache 튜닝, 별도 임포터 JVM, **Python 임베드 불가**(항상 서버). 단 단일 Docker + `neo4j-admin import` 재구축 [S9][S10][S12][S16] **[검증]** | 가볍다: 단일 컨테이너/바이너리, 인메모리라 재로드 빠름. 단 **RAM 바운드(~2× RAM 권장)**, 임베드 불가 [S17][S19][S28] **[검증]** | **가장 가벼움**(JVM 없음, `COPY` 로드, `DROP`/재생성). 단 **3종 묶음 공식 이미지 없음**, 커넥션마다 `LOAD 'age'` 마찰 [S32][S40] **[검증]** · 셋 모두 Python 임베드 불가 |
| **⑤ 라이선스 & 비용** (OSS 선호) | Community = **GPLv3(OSI)**. **벡터 인덱스·GDS 무료**(Enterprise 게이트는 native `VECTOR` block-format·클러스터·RBAC·멀티DB·핫백업뿐) [S1][S2][S4][S7][S8] **[검증]** | Community = **BSL(소스공개형, 비-OSI)**. 벡터·MAGE 무료 [S27] **[검증]** | **전 스택 완전 OSS·게이팅 없음**(PostgreSQL License + Apache-2.0) [S32][S34] **[검증]** |
| **⑥ 성숙도·운영준비·커뮤니티** | **최고**: 2007년~, DB-Engines 그래프 #1, GitHub ~16.8k★ [S46] **[검증]** | 신생(2016~), DB-Engines #13, ~4.2k★. **GraphRAG/AI-memory에 정조준**해 빠르게 성숙 [S46] **[검증]** | PG코어·pgvector 매우 성숙; **AGE가 약한 고리**(~4.6k★, PG 메이저 지원 지연) [S46][S34] **[검증]** |

## 2. 후보별 평가

### Neo4j
**강점.** 단일-DB GraphRAG의 정석에 가장 가깝다. 벡터 인덱스(5.11+, Lucene HNSW, ≤4096차원)와 Cypher 순회가 한 DB에 있고 [S1], `VectorCypherRetriever`는 "벡터로 시드 노드를 찾고 거기서 Cypher 순회·확장"을 한 번에 한다 [S47] — palimpsest의 "최근 푸시가 내 설계를 위협하나?"(시드→의존 설계 노드 순회) recall 패턴과 정확히 맞는다. Leiden/Louvain은 GDS에 무료, Python GraphRAG 툴링이 셋 중 최다 [S5][S13][S14][S15].

**진짜 약점/주의.**
- **운영 무게(④)가 최대 약점.** JVM heap + off-heap page cache 튜닝, 벌크 임포트도 자체 JVM [S10][S12]. "가볍게 띄웠다 버리는" 투영엔 셋 중 가장 무겁다.
- **Python 임베드 불가.** Python 드라이버는 Bolt로 서버 접속 [S9][S16] — 항상 서버 프로세스 필요(셋 다 동일하나 Neo4j가 가장 무겁다).
- **Enterprise 게이팅(정정).** 벡터 인덱스·GDS 커뮤니티 탐지는 **무료**. Enterprise 전용은 native `VECTOR` block-format·클러스터·RBAC·멀티DB·핫백업 [S1][S2][S8] — v1 슬라이스에 필수인 것 없음.
- **라이선스.** Community는 GPLv3 [S7]. Bolt로 별도 프로세스에 접속하는 한 클라이언트에 copyleft 전파 안 된다는 게 통상 이해이나 **추론** — 배포 형태에 따라 법무 확인 필요.

**불확실.** (a) Community `LIST<FLOAT>` 임베딩 저장이 Enterprise block-format 대비 대규모 지연/메모리 페널티를 주는지 **미검증**. (b) GDS 4코어 제한이 우리 그래프에서 Leiden 실행시간에 주는 영향 미측정. (c) `neo4j-graphrag` KG 빌더의 최소 Neo4j 버전 문서 미확인.

### Memgraph
**강점.** 인메모리 우선이라 스핀업·재로드 빠르고 운영 표면 작다 [S28]. 네이티브 벡터 인덱스(USearch/HNSW) GA, Cypher 통합 [S20][S21], 3.8에서 벡터 메모리 대폭 감소 [S22]. MAGE에 Leiden/Louvain 무료 [S26], Bolt 호환이라 **공식 neo4j Python 드라이버 그대로 사용** [S24]. 회사가 GraphRAG/AI-memory/코드그래프 recall을 명시 타깃 [S31].

**진짜 약점/주의.**
- **RAM 바운드.** 작동 그래프+벡터가 RAM에 들어가야 하고 권장은 데이터 **~2× RAM** [S19]. 코드 KG가 커지면 메모리 천장이 실질 위험.
- **라이선스 BSL(비-OSI)** [S27]. "오픈소스 선호" 기준에서 Neo4j(GPLv3)·Postgres보다 약함. MAGE도 BSL로 이동 [S25].
- **Cypher 비호환 함정.** `shortestPath()` 대신 `-[*BFS]-` 등, 일부 서브쿼리·함수 미지원 [S23] — Neo4j 쿼리 그대로 이식 불가.
- **내구성은 설정 선택.** ANALYTICAL 모드는 자동 영속화 없음(수동 snapshot) [S17][S18] — 투영엔 맞을 수 있으나 의식적 설정 필요.

**불확실.** 벡터 검색 정확한 GA 버전, 병합 후 MAGE 현행 라이선스 조항 [S25].

### Postgres + pgvector + Apache AGE
**강점.** 운영 베이스라인 가장 가벼움(JVM 없음, `COPY` 로드, `DROP`/재생성 싸고 스크립트화 쉬움) [S32]. 전 스택 완전 OSS·게이팅 전무 [S32][S34]. pgvector 성숙(0.8.x, HNSW+IVFFlat) [S32][S33], "이미 Postgres를 안다"는 도입 이점(추론).

**진짜 약점/주의.**
- **커뮤니티 탐지 부재(②).** Leiden/Louvain을 외부(networkx/igraph)에서 돌려야 함 [S34][S35] — GraphRAG 핵심 빌딩블록을 1일차부터 DB 밖으로.
- **조립형 통합 마찰(①).** 두 확장이라 `agtype` 캐스팅·`search_path`·`LOAD 'age'`가 쿼리마다 [S37].
- **AGE가 약한 고리.** PG 메이저별 별도 빌드, 지원 버전 표기 엇갈림, 작은 커뮤니티 [S34]. 3종 묶음 공식 이미지 없음 → 커스텀 Dockerfile [S40].
- **Python GraphRAG 툴링 최약체** [S39][S41].

**불확실.** AGE 릴리스 날짜 출처 충돌; 그래프 순회 성능은 전용 엔진 대비 약해 보이나 독립 벤치 안 함.

## 3. 권고

**첫 빌드는 Neo4j(Community)로 시작하고, Memgraph를 명시적 폴백으로 함께 벤치한다. Postgres+AGE는 첫 기반에서 제외하되 후속 옵션으로 남긴다.**

- **최우선 ①**: Neo4j·Memgraph 모두 그래프+네이티브 벡터를 한 DB에서 성숙하게 제공. Postgres+AGE는 "두 확장 조립"이라 통합 성숙도 명확히 낮음 [S37] → Neo4j·Memgraph로 1차 좁힘.
- **②·③·⑥에서 Neo4j 우세**: 커뮤니티 탐지(GDS Leiden 무료) [S2][S3], Python GraphRAG 툴링 [S5][S13][S14], 성숙도(DB-Engines #1) [S46].
- **⑤(라이선스)**: 통념을 뒤집는 핵심 — Neo4j 벡터·GDS는 Community 무료 [S1][S2]. "Neo4j는 Enterprise 강제"라는 반대 사유가 v1엔 미적용. Memgraph는 BSL이라 한 칸 약함.
- **유일하게 지는 ④(운영 무게)**: 실재하나 한정적 — 단일 Docker + `neo4j-admin import`로 운용 가능, JVM 튜닝은 관리 가능한 비용 [S11][S12]. "검증된 무거움"을 "신생 BSL/RAM 제약"·"조립형 미성숙"보다 첫 빌드에 받아들일 만하다(추론).

**Postgres+AGE 제외 이유**: 단일-DB 통합 최약(①), 커뮤니티 탐지 부재로 외부 클러스터링 강제(②), 툴링 최약(③). 장점(순수 OSS·경량·익숙함)은 진짜지만 "커뮤니티 요약형 GraphRAG" 출발 기반으론 부적합. **단, 워크로드가 얕고 벡터 중심으로 판명되면 합리적 대안.**

**오직 실측만 답할 수 있는, 권고를 뒤집을 수 있는 질문:**
1. **Neo4j** — Community `LIST<FLOAT>` 벡터 저장이 우리 코퍼스 규모에서 유의미한 페널티? GDS 4코어 제한이 Leiden을 실용 불가로?
2. **Memgraph** — 실제 RAM이 예산 안에? git 전체 재구축이 Neo4j보다 충분히 빨라 BSL·RAM 리스크를 상쇄?
3. **공통** — 교차브랜치 설계위험 쿼리의 순회 지연(p50/p95), 벡터 recall@k, 커밋 배치 ingest 시간이 의미 있게 갈리나?

## 4. 나중에 실측 벤치가 반드시 측정할 것

**현재 실측 불가**: v1 슬라이스 스키마도 데모 코퍼스도 없다. 지금 결론은 "문서·통합 성숙도 기반 좁히기"이며 성능·메모리·재구축 우열은 **미검증**.

**전제물:** ① **최소 슬라이스 스키마** — 엔티티(Commit, File, Function/Module, DesignDecision), 관계(`MODIFIES`/`CALLS`/`DEPENDS_ON`/`RISKS`/`AUTHORED_BY`), provenance(commit SHA, author, ts), freshness(last_seen). 임베딩 차원 고정(768/1536 — 3072는 pgvector HNSW 2000차원 상한에 걸려 halfvec 필요 [S32], Neo4j는 4096 [S1]). ② **데모 코퍼스** — 실제 repo 1개 최근 커밋 ~500개를 KG로 투영(엔티티/관계/커뮤니티 수 고정 → 세 후보 동일 입력).

**워크로드(v1 슬라이스 그대로):** (a) git→KG ingest, (b) 변경 심볼→의존 설계 노드 k-hop 순회 + 벡터 유사 시드 결합, 근거(커밋) 인용 포함 답변.

| 지표 | 정의 |
|---|---|
| Ingest 시간 | git→KG 전체 빌드(커밋/초) |
| 순회 지연 | 변경 심볼→의존 설계 노드 k-hop, p50/p95 |
| 벡터 검색 recall/지연 | top-k 엔티티, brute-force 대비 recall@k, p50/p95 |
| 커뮤니티 탐지 런타임 | Leiden: Neo4j(4코어) vs Memgraph MAGE vs AGE(외부 networkx) — Postgres의 구조적 약점을 정량화 |
| 전체 재구축 시간 | 투영 drop 후 git에서 재구축(= git=SoT teardown/rebuild) |
| 메모리 풋프린트 | 피크 RAM |
| 답변 groundedness | 인용이 실제 커밋으로 해소되는 비율 |

이 표가 채워지기 전까지 Neo4j-우선 권고는 "성숙도·통합·라이선스 기반의 합리적 출발점"이지 "성능 우위가 실측된 선택"이 아니다.

### 4.1 as-built 실측 (단일 substrate, wi_260702sfd)

**주의: 이것은 §4의 3-DB 비교가 아니다.** as-built 파이프라인(Neo4j 5 Community, 벡터층 없음 — 임베딩은 유예된 C-결정)을 실제 코퍼스로 실측한 단일 substrate 값이다. Memgraph·Postgres 대조·벡터 recall은 미측정. 하네스·원자료: `bench/benchmark.py` → `bench/results/asbuilt-neo4j.json`(재현: `.venv/bin/python bench/benchmark.py --repo <repo> --total-commits <N>`).

- **코퍼스/방법:** EcoleTreeSystems 최근 45커밋 윈도(전체 425). 최근 커밋은 그래프가 ~HEAD 크기라 **최악(가장 느린) ingest 레이트**를 잡는 보수적 측정. 전체 이력 절대시간은 wall-clock이 아니라 rate 외삽.
- **Ingest:** 0.073 commits/sec (~13.7s/커밋), HEAD 1439 nodes / 8146 edges. 전체 425 외삽 ≥ **~97분**(하한 — 아래 finding대로 superlinear).
- **회상 순회 지연:** depth1 p50 24ms / p95 47ms, depth2 p50 35ms / p95 59ms (30 seed × 5 iter). 읽기 경로는 빠르다.
- **전체 재구축:** teardown(DETACH DELETE) 0.24s + backfill 563s(45커밋). 빈 DB backfill과 사실상 동일 비용(MERGE 멱등).
- **피크 RAM:** Neo4j 컨테이너 946 MiB.

**Finding — ingest가 superlinear로 느린 이유(증거 기반):** `kg/ingest.py`의 관계 MERGE가 끝점을 무라벨 `MATCH (a {id: row.src})`로 찾는다. 전역(무라벨) id 인덱스가 없어(`SHOW INDEXES`: 라벨별 RANGE 인덱스만 존재) 플래너가 인덱스를 못 쓰고 **전체 노드 스캔**으로 떨어진다. 커밋마다 ~8000엣지 × 2 끝점 스캔이고 그래프가 커질수록 스캔 비용이 커져 backfill이 커밋 수에 대해 초선형이 된다(초반 ~5s/커밋 → 그래프 성장 후 ~13s/커밋 관측). **회상(읽기)은 라벨 있는 seed 조회라 이 문제에서 자유롭다.** 최적화(예: 전역 id 인덱스 또는 끝점 MATCH에 라벨 부여)는 별도 work item 후보 — 이 벤치의 범위 밖.

## 5. 출처 (Sources)

**Neo4j** — [S1] https://neo4j.com/docs/cypher-manual/current/indexes/semantic-indexes/vector-indexes/ · [S2] https://neo4j.com/docs/graph-data-science/current/introduction/ · [S3] https://neo4j.com/docs/graph-data-science/current/algorithms/leiden/ · [S4] https://github.com/neo4j/graph-data-science · [S5] https://pypi.org/project/neo4j-graphrag/ · [S6] https://neo4j.com/docs/neo4j-graphrag-python/current/ · [S7] https://neo4j.com/product/community-edition/ · [S8] https://neo4j.com/docs/operations-manual/current/introduction/ · [S9] https://neo4j.com/docs/java-reference/current/java-embedded/setup/ · [S10] https://neo4j.com/docs/operations-manual/current/performance/memory-configuration/ · [S11] https://hub.docker.com/_/neo4j · [S12] https://neo4j.com/docs/operations-manual/current/import/ · [S13] https://neo4j.com/blog/developer/microsoft-graphrag-neo4j/ · [S14] https://developers.llamaindex.ai/python/examples/property_graph/graph_store/ · [S15] https://pypi.org/project/langchain-neo4j/ · [S16] https://neo4j.com/docs/api/python-driver/current/ · [S47] https://neo4j.com/docs/neo4j-graphrag-python/current/api.html

**Memgraph** — [S17] https://memgraph.com/docs/fundamentals/data-durability · [S18] https://memgraph.com/docs/deployment/workloads/memgraph-in-graphrag · [S19] https://memgraph.com/docs/fundamentals/storage-memory-usage · [S20] https://memgraph.com/docs/querying/vector-search · [S21] https://memgraph.com/blog/simplify-data-retrieval-memgraph-vector-search · [S22] https://memgraph.com/docs/release-notes · [S23] https://memgraph.com/docs/querying/differences-in-cypher-implementations · [S24] https://memgraph.com/docs/client-libraries/python · [S25] https://github.com/memgraph/mage · [S26] https://memgraph.com/docs/advanced-algorithms/available-algorithms/leiden_community_detection · [S27] https://memgraph.com/pricing · [S28] https://memgraph.com/docs/getting-started/install-memgraph/docker · [S29] https://developers.llamaindex.ai/python/examples/property_graph/property_graph_memgraph/ · [S30] https://python.langchain.com/docs/integrations/graphs/memgraph/ · [S31] https://memgraph.com/docs/ai-ecosystem/graph-rag

**Postgres+pgvector+AGE** — [S32] https://github.com/pgvector/pgvector · [S33] https://www.postgresql.org/about/news/pgvector-080-released-2952/ · [S34] https://github.com/apache/age · [S35] https://age.apache.org/faq/ · [S36] https://github.com/apache/age/releases · [S37] https://techcommunity.microsoft.com/blog/adforpostgresql/combining-pgvector-and-apache-age---knowledge-graph--semantic-intelligence-in-a-/4508781 · [S38] https://yonk.dev/blog/graphrag-part2-postgres-age-pgvector/ · [S39] https://python.langchain.com/docs/integrations/graphs/apache_age/ · [S40] https://hub.docker.com/r/apache/age · [S41] https://github.com/apache/age/issues/1783

**GraphRAG 툴링/성숙도** — [S42] https://github.com/microsoft/graphrag · [S43] https://www.microsoft.com/en-us/research/blog/moving-to-graphrag-1-0-streamlining-ergonomics-for-developers-and-users/ · [S44] https://arxiv.org/pdf/2404.16130 · [S46] https://db-engines.com/en/ranking/graph+dbms · [S48] https://github.com/kuzudb/kuzu (참고: 유일한 진짜 임베디드 graph+vector, 후보 아님)
