# ADR-20260701-v1-ontology-recall-reframe — v1 첫 슬라이스 재프레임: 설계위험 감지 → 온톨로지 + grounded 회상

- 식별자: `ADR-20260701-v1-ontology-recall-reframe` (파일명 = 불변 식별자)
- 상태: active
- 날짜: 2026-07-01
- work item: wi_2606263sn (deep-interview 재개 → 재프레임 → autopilot 구현·검증)
- 관계: `ADR-20260626-foundational-architecture`를 구체화하고, VISION.md §다음단계 #1의 "v1 첫 슬라이스 = 브랜치 간 설계위험 감지"를 **supersede**한다.

## 맥락

정초 1회차 deep-interview(2026-06-26)는 v1 첫 슬라이스를 "브랜치 간 설계위험 감지"로 기록했다(VISION §다음단계 #1). deep-interview 재개(2026-07-01) 중 사용자가 이 프레이밍에 의문을 제기했다 — "우리가 하려는 게 위험 검출인가, 아니면 pre-compute·HugRAG·GraphRAG로 프로젝트 온톨로지를 구축해 코드베이스 이해·컨텍스트 조립을 하는 것인가?"

검토 결과 "설계위험 감지"는 본체(KG 온톨로지 + GraphRAG 회상)의 한 응용(5기능 중 Reconcile)을 첫 슬라이스로 고른 것이었고, 세 가지가 재프레임을 뒷받침했다: ① 온톨로지+회상 본체가 모든 응용(위험 감지 포함)의 선결 의존이다 ② 사용자는 대상 코퍼스에 개입하지 않아 위험 ground-truth를 검증할 수 없으나, "온톨로지가 구축되고 근거 붙은 컨텍스트를 회상한다"는 도메인 지식 없이 구조적으로 검증된다 ③ 도입하려던 개념(Meta pre-compute·HugRAG·GraphRAG)이 전부 이해·컨텍스트·온톨로지 지향이다.

## 결정

1. **v1 deliverable = KG 온톨로지 구축 + GraphRAG 근거결박 점진 회상.** 코드베이스를 캡처→KG 온톨로지(Preserve+Relate)로 만들고, 질의하면 그래프 순회로 관련 코드·의존을 출처(커밋/file:line) 붙여 점진 회상한다(Recall+Curate 조합형). 성공 기준 = 온톨로지 구축·동작 보장; 회상 퀄리티·랭킹은 유예.
2. **설계위험 감지는 slice 2로 유예.** 전이적 결합(CALLS/DEPENDS_ON) 순회는 v1에서 "위험 판정"이 아니라 "구조적 관계/영향 회상"으로 실현된다; "위험" 판정 라벨은 다음 슬라이스에서 이 본체 위에 얹는다.
3. **v1은 결정론적 구조층만.** 생성형 LLM 합성·semantic 요약(GraphRAG community report)·inferred 엣지는 유예. Curate는 조합형만(회상 경로에 LLM 없음).

## v1에서 실현·검증된 기술 결정 (code = SoT: `src/palimpsest/`)

- **정적 추출**: tree-sitter-java(py-tree-sitter) → IR. `Repo/Package/File/Class/Method` 노드 + `CONTAINS/CALLS/DEPENDS_ON/IMPORTS` 엣지, git provenance. CALLS는 이름기반 best-effort(타입해소 없음, 정밀도 유예).
- **KG(Neo4j Community)**: 라벨별 uniqueness CONSTRAINT + `id`(=qualified_name) MERGE 멱등 ingest. `Episode`=commit 노드. provenance(commit SHA/author/committed_at) + `code_bound_at`(v1 단일커밋=committed_at).
- **`edge_kind='deterministic'`**: 모든 엣지에 부착. Neo4j Community는 관계 속성 존재 제약을 못 걸어 **DB 스키마가 아니라 writer + 테스트로 강제**(catch-all NULL=0). 향후 inferred 엣지 writer는 이 분리를 규약으로 지켜야 한다.
- **회상**: bounded 무방향 BFS(depth+limit 예산) + expand-handle 점진 pull. 각 item에 커밋+file:line 출처, 미해소 seed→명시 gap(confident-empty 아님), confidence=grounding 커버리지. **회상 경로에 생성형 라이브러리 0**(fresh 인터프리터 probe 검증).
- **노출**: CLI(`python -m palimpsest ingest|query`). MCP/스킬은 유예.
- 검증: 실제 EcoleTree 코퍼스 158파일 end-to-end(1454노드/8075엣지, provenance=코퍼스 커밋) + 18 테스트 green + 독립 verify로 ac-1/2/3 pass.

## 철회·변경 조건 (change_condition)

- "온톨로지+회상이 v1"이라는 정체성은 비전 차원 재정의에서만 재론한다. ADR-20260626의 본체(KG+GraphRAG)와 정합하며 그 부분집합이다.
- 실현된 기술 선택(tree-sitter-java·Neo4j·edge_kind 구성강제)은 git=SoT라 재구축 가능한 projection이므로 성능·정밀도 근거가 나오면 교체 가능(코드가 권위).
