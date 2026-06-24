# START HERE — palimpsest 시작 지도

이 repo를 처음 여는 사람/에이전트를 위한 지도다. 무엇이 이미 결정됐고, 무엇이 열려 있고, 어디서부터 시작하는지를 담는다.

## 이 repo는 무엇인가

**palimpsest** = 한 프로젝트의 라이프사이클(의사결정 → 코드 반영, 코드 뒤에 비치는 기획·설계·구현 의도와 관계)을 투명하게 드러내는 **장기기억·지식 큐레이터**.

현재 상태: **seed / 정초.** 정체성·방향·잠긴 결정만 박혀 있고 **런타임 동작은 아직 없다**(의존성·코드 없음).

## 읽는 순서

1. `README.md` — 한눈 요약
2. `docs/VISION.md` — **정초 SoT.** 이름·목적·5기능·잠긴 결정·스택·다음 단계
3. 이 문서(`START_HERE.md`) — 시작 경로 · 출처 · 첫 행동

## 이미 잠긴 것 (재론의 대상 아님)

`docs/VISION.md`에 근거와 함께 있다. 요지:

- **별도 standalone 프로젝트** (ditto 내부가 아님)
- **git = SoT**, 그래프 DB = 재구축 가능한 투영, 벡터는 보완
- **큐레이터 이원 능력** — 조합형(계보·여정 구성) + 생성형(근거 결박 답 합성) **둘 다 1급**. 단 생성형은 출처 + 모르는 것(gap) + confidence로 사실과 분리(세탁 금지)
- **seam 대체** — ditto의 memory 동작 지점을 palimpsest 호출로 대체(기능 이식/feature parity 아님). palimpsest가 장기적으로 ditto memory를 흡수, ditto는 소비자
- **스택**: Python(GraphRAG·RAG·그래프·LLM 생태계 지배) + Neo4j 1차(그래프+네이티브 벡터=GraphRAG 단일 DB, 택일은 첫 스파이크에서 실측) + ditto엔 MCP 서버로 노출
- **스코프**: 1차 = 프로젝트 단위 + git 브랜치 간(개인↔팀), 조직·cross-repo는 확장 축

## 아직 열린 것 = 첫 할 일

palimpsest 자체 deep-interview로 결정할 6항목(`docs/VISION.md` §다음 단계):
MVP(개인 vs 조직) · 스키마(엔티티·관계·provenance·신선도) · 캡처/회상 메커니즘 · DB 택일 스파이크 · MCP vs ditto-pluggable · 벡터/RAG 설계.

**권장 첫 행동 두 가지(병행):**
1. **DB 택일 스파이크** — Neo4j vs Memgraph vs PostgreSQL(pgvector + Apache AGE). git=SoT라 DB는 교체 가능한 투영이지만, 기반이 되는 선택이라 실측으로 먼저 좁힌다(GraphRAG를 단일 DB에서 돌릴 수 있는가가 1차 기준).
2. **MVP 범위 deep-interview** — 개인부터 vs 팀/조직부터. 5기능 중 무엇을 첫 슬라이스로 세울지.

## 출처 / 배경 (외부 의존 — 읽어둘 것)

palimpsest는 **ditto**라는 별도 repo에서 갈라져 나왔다. 정초의 출처 자료는 palimpsest 밖, ditto repo 안에 있다:

- ditto `memory-system.md` — 사용자 원본 비전 노트(도서관/사서 은유, 목적·목표·고려사항)
- ditto `reports/design/memory-librarian-external-seed-spec.md` — 시드 스펙(위 잠긴 결정 ①~⑥의 원본)
- ditto `ADR-0021` (`.ditto/knowledge/adr/`) — 권위 있는 결정 기록. **충돌 시 ADR-0021이 우선**
- ditto `reports/research/agent-memory-systems-comparative.md`, `gbrain-code-level-research.md` — GBrain·claude-mem·ditto memory 비교 연구(생성형 합성 모델을 차용한 맥락)

**이 자료들의 요지는 `docs/VISION.md`에 흡수했으므로 시작에 원본이 필수는 아니다.** 깊은 배경이 필요할 때만 ditto repo를 참조한다. **이 시점부터 palimpsest는 self-contained하게 자체 결정 기록을 쌓는다** — 외부 경로에 권위를 의존하지 않는다(첫 ADR을 palimpsest 안에 만드는 것이 그 시작).

## 작업 방식

- **git = SoT.** 되돌리기 어려운 결정은 palimpsest 자체 ADR로 기록한다(첫 ADR 후보 = 스택 확정 또는 DB 택일).
- **근거 결박.** 모든 주장·합성 출력은 출처 + gap을 명시하고, 확신 없는 것을 사실로 굳히지 않는다 — 이 프로젝트의 존재 이유(할루시네이션 최소화) 그 자체다.
- 의존성은 첫 스파이크에서 추가한다(`pyproject.toml`의 `dependencies`는 현재 의도적으로 비어 있음).

## 현재 repo 구조

```
README.md                  한눈 요약
START_HERE.md              이 지도
docs/VISION.md             정초 SoT (이름·목적·5기능·잠긴 결정·스택·다음 단계)
pyproject.toml             패키지 메타 (deps 비어 있음 — 첫 스파이크에서 채움)
src/palimpsest/__init__.py 빈 패키지 (런타임 없음)
.gitignore
```
