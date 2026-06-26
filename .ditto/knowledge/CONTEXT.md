# palimpsest — DITTO Knowledge Context

Durable project knowledge lives under `.ditto/knowledge/`. This file is seeded
empty by `ditto init`; DITTO's knowledge-update flow appends durable decisions,
agreed terms, and learnings over time.

- glossary: `glossary.json`
- decisions: `adr/`

## 합의 용어 (glossary headline)

- **palimpsest** — 코드 표면 아래 의사결정·의도 층을 다시 읽게 하는 Knowledge Graph 기반 장기기억·지식 큐레이터.
- **Knowledge Graph (본체)** — palimpsest의 핵심 표현(엔티티+관계+온톨로지+provenance+신선도, 모든 엔티티 1급).
- **GraphRAG (회상층)** — KG 위 그래프탐색+벡터+LLM 합성 회상층, 출력은 근거 결박(출처+gap+confidence).
- **design-risk slice** — v1 첫 수직 슬라이스(브랜치 간 설계위험 감지).

전체 정의는 `glossary.json` 참조.

## 결정 (ADR headline)

- **ADR-20260626-foundational-architecture** (active) — palimpsest 정초 아키텍처: 본체 = Knowledge Graph, 회상·합성 = GraphRAG(KG 의존), 이력 전부 보존, 캡처 자동 기본. → `adr/ADR-20260626-foundational-architecture.md`

## v1 초점

v1 초점 = **design-risk slice**: 구조·동작을 보장하고 위험판정 퀄리티는 다음 단계로 둔다. 전체 슬라이스 명세는 `.ditto/local/work-items/wi_2606263sn/intent.json` 참조(여기서 중복하지 않음).
