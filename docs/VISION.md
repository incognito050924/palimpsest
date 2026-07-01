# palimpsest — 비전 / 정초 문서

> 상태: **seed / 정초(scaffold)**. 이 문서는 프로젝트의 정체성(이름·목적·기능)과 *이미 잠긴 전제*를 박는다.
> MVP 범위·스키마·캡처/회상 메커니즘·DB 택일·통합 형태는 **palimpsest 자체 deep-interview**로 결정한다(아래 §다음 단계).
> **정초 deep-interview 완료(wi_2606263sn):** Knowledge Graph 본체 + GraphRAG 회상층, 모든 엔티티 1급, 전이력 보존(버려진 대안 포함), 자동 캡처가 확정됐다(1회차 2026-06-26). **v1 첫 슬라이스는 재프레임됐다(2026-07-01, ADR-20260701): 코드베이스 → KG 온톨로지 구축 + GraphRAG 근거결박 회상**(설계위험 감지는 slice 2로 유예). **권위는 ADR `ADR-20260626-foundational-architecture`·`ADR-20260701-v1-ontology-recall-reframe`와 IntentContract `intent.json`이며, 이 문서는 그 해소 상태를 비추는 배경 지도다(§다음 단계).**
> 출처(provenance): ditto repo의 사용자 원본 메모(`memory-system.md`), 시드 스펙(`reports/design/memory-librarian-external-seed-spec.md`), `ADR-0021`, gbrain 비교 연구. 충돌 시 ditto `ADR-0021`이 권위.

---

## 이름

**palimpsest** — 양피지에 썼다 지우고 다시 쓴 사본. 지워진 **이전 층이 그 아래로 비쳐 보이는** 것을 뜻한다.

코드베이스는 정확히 palimpsest다: 표면의 코드 아래에 요건·기획·설계·구현을 반복하며 쌓인 의사결정·의도·고민의 층이 비쳐 있으나, 잘 드러나지 않는다. 이 시스템의 목적은 그 **아래 층을 다시 읽을 수 있게** 만드는 것이다.

세계관 정합: 짝이 되는 도구 **ditto**(〃, "위와 같음/반복")와 결이 같다 — 둘 다 *"이전에 쓰인 것과의 관계"*를 핵심에 둔 고전 어휘다(ditto=반복·동일, palimpsest=누적·비침).

## 목적

> 한 프로젝트의 **라이프사이클 전체** — 발생한 무수한 의사결정과 그것이 코드에 어떻게 반영됐는지, 코드에는 잘 드러나지 않는 **기획·설계·구현 당시의 의도와 관계성** — 을 투명하게 드러내는 **장기기억·지식 큐레이터**.

큐레이터다(단순 아카이브/사서가 아니다): 보관·전달을 포함하되 그 위에 소장 자료를 활용해 **새 가치를 합성**한다 — 흩어진 자료를 엮어 결정 계보·여정 구조를 구성하고(조합형), 근거에 결박된 답을 합성한다(생성형).

세 목표:
1. **LLM·에이전트 할루시네이션 최소화** — 근거 있는 지식 베이스로 grounding 한다.
2. **context rot 없는 점진적 컨텍스트 획득** — 장기기억을 한 번에 로드하지 않고, 필요한 시점에 필요한 부분만 점진적으로 회상한다. 토큰·컨텍스트 윈도우 비용 최적화가 1급 제약이다.
3. **의사결정·의도 투명화** — 코드 뒤의 의도가 한 사람·소수에게만 있으면 다른 누군가는 그 의도와 충돌하는 작업을 한다. 모든 의도·의사결정·모호성을 드러낸다.

원칙: 모든 결과는 **명확한 근거**를 갖고, 논리적 모순·거짓이 없어야 하며, 확실하지 않은 것은 섣부른 가정 없이 **명시적으로 드러낸다**. 기존 의사결정·규칙은 항상 반영한다.

## 핵심 기능 (5)

1. **Preserve(보존)** — git을 원본(SoT)으로, provenance와 신선도를 보존한다. 커밋 해시 기반 2축 신선도로 *"이 지식이 이 커밋 기준 아직 live한가"*를 답한다.
2. **Relate(관계 투영)** — 코드·결정·의도·여정 사이의 관계를 그래프로 투영해 질의한다(엔티티·함수·데이터·결정 간 흐름). 벡터는 보완.
3. **Recall(점진 회상)** — 한 번에 로드하지 않고 필요한 부분만 점진적으로 가져온다(context rot 회피).
4. **Curate(합성)** — 조합형(자료를 엮어 결정 계보·여정 구성) + 생성형(근거에 결박된 답 합성). 생성형 출력은 반드시 **출처 + 모르는 것(gap) 명시 + confidence 계급으로 사실과 분리**한다(세탁 방지).
5. **Reconcile(컨텍스트 중재)** — git 브랜치(개인↔팀) 간 컨텍스트는 **차이가 날 수밖에 없음**을 1급으로 인정하고, 그 차이를 드러내며 **무엇이 더 신선한지·무엇을 우선할지** 판정한다.

## 스코프

- **1차: 프로젝트 단위 + git 브랜치 간(개인↔팀) 컨텍스트.** 한 프로젝트의 라이프사이클과 브랜치 간 신선도 중재부터.
- **확장 축: 조직·cross-repo(다중 저장소).** 시드(ADR-0021)가 1차 스코프로 잠근 조직·cross-repo는 폐기가 아니라 이후 확장으로 남긴다. ("개인부터 vs 조직부터" MVP 택일은 시드가 palimpsest로 위임 → §다음 단계)

## 잠긴 결정 (재론의 대상 아님 — ditto `ADR-0021` + 시드 ①~⑥)

1. **별도 standalone 프로젝트.** ditto 내부가 아니라 분리. (서버형·cross-repo는 ditto의 무서버·git-native·단일 repo 기층과 맞지 않음)
2. **git = SoT + 그래프 투영(벡터 보완).** 원본은 git, 그래프 DB는 재구축 가능한 read-model, 벡터는 보완.
3. **조직·cross-repo 스코프**(장기). 1차는 프로젝트 단위로 시작(위 §스코프).
4. **seam 대체(기능 이식 아님).** ditto에서 memory가 동작하던 층위를 palimpsest 표면 호출로 갈아끼운다. feature parity는 비목표.
5. **palimpsest가 장기적으로 ditto memory를 흡수, ditto는 소비자.** 전환은 fail-closed(능력 실증 → seam 연결 → 현 동작 deprecate).
6. **큐레이터 이원 능력 — 조합형 + 생성형 둘 다 1급.** (ditto 내부 큐레이터가 생성형을 INFERRED로 한정한 것과 달리, standalone이라 advisory 자세에 구속되지 않음. 단 생성형은 출처+gap+confidence로 사실과 분리 — 세탁 금지.)

## 기술 스택 (결정 + 근거)

선호가 아니라 **생태계 지배력 + 구조 단순성**으로 결정한다.

- **언어: Python.** 이 시스템의 본질(GraphRAG·RAG·임베딩·LLM 합성·그래프 구축)의 생태계가 Python에 압도적으로 몰려 있다(MS GraphRAG, neo4j-graphrag, LlamaIndex, LangChain, sentence-transformers, 커뮤니티 검출). 다른 언어는 미성숙 포트 의존 또는 직접 구현으로 구조가 복잡해진다.
- **그래프 DB: Neo4j(1차 추천).** 그래프 1급 + 네이티브 벡터 인덱스로 GraphRAG를 단일 DB에서. **택일(Neo4j / Memgraph / PostgreSQL+pgvector+AGE)은 첫 스파이크에서 실측 확정** — git=SoT 모델상 DB는 교체 가능한 projection이라 잠그지 않는다.
- **ditto 통합: MCP 서버 분리.** Python↔ditto(TS) 언어 이질은 MCP(JSON-RPC) 경계로 해소 — 구조 복잡으로 이어지지 않고 깔끔한 seam. (MCP vs ditto-pluggable 최종 택일은 §다음 단계로 위임)

## ditto와의 관계

ditto는 palimpsest의 **소비자**다. palimpsest는 MCP 서버(또는 ditto-pluggable 표면)로 노출되고, ditto는 memory가 동작하던 seam을 palimpsest 호출로 대체한다(기능 이식이 아니라 seam 대체). 전환 전까지 현 ditto memory 동작은 유지된다(seam 연속성, ADR-0021 D4).

## 비목표

- 기존 ditto memory 기능의 온전한 이식 / feature parity (흡수는 seam 대체이지 기능 보존이 아님).
- **선호 때문에 구조를 복잡하게 만드는 것** — 생태계가 지배적인 스택을 따른다.
- 정초 단계에서 MVP·스키마·메커니즘·DB·통합형태를 못 박는 것(아래로 위임).

## 다음 단계 (palimpsest 자체 deep-interview로 위임)

> 1회차 deep-interview(wi_2606263sn, 2026-06-26)의 해소 상태를 각 항목에 표시한다. 권위: `ADR-20260626-foundational-architecture`, `intent.json`. 미정 항목은 이후 deep-interview/스파이크로 좁힌다.

1. **MVP 범위** — ✅ **확정(2026-07-01 재프레임, ADR-20260701).** v1 첫 슬라이스 = **코드베이스를 KG 온톨로지로 구축 + GraphRAG 근거결박 점진 회상**(질의하면 관련 코드·의존을 출처 붙여 회상). 성공 기준 = 온톨로지 구축·동작 보장, 회상 퀄리티는 다음. **설계위험 감지는 slice 2**(이 본체 위에 얹음). shipped·검증 통과: `src/palimpsest/`(extract→kg→recall→cli). (intent.json)
2. **스키마** — ◐ **부분.** Knowledge Graph·모든 엔티티 1급·전이력 보존 확정(ADR). 상세 엔티티/관계 모델과 v1 슬라이스용 최소 온톨로지는 미정.
3. **캡처/회상 메커니즘** — ◐ **부분.** 자동 캡처 기본(실시간/배치/사용자 지시 혼합)·회상 pull 우선 확정. 상세 수집/점진 회상 메커니즘은 미정. (push 능동경고·전체 backfill·에이전트 트레이스 캡처는 확장축)
4. **그래프 DB 택일 스파이크** — ◐ **1차 좁힘**(wi_2606264gw, 리포트 `docs/spikes/db-substrate-spike.md`). 문서 근거상 **Neo4j Community 1차 + Memgraph 폴백**, Postgres+AGE 제외(커뮤니티 탐지 부재·조립형 미성숙). 통념과 달리 Neo4j 벡터 인덱스·GDS 커뮤니티 탐지는 Community 무료. **성능·메모리·재구축 실측은 스키마+코퍼스 확보 후** 미결.
5. **통합 형태 택일** — ⬜ 미정(노출면 한정). MCP 서버 vs 스킬 등록 vs ditto-pluggable — **v1에서도 열어 둔다.** 핵심(KG·GraphRAG)은 노출 메커니즘에 독립이고 노출은 얇은 어댑터다. Python 주언어는 어느 쪽도 막지 않음(MCP Python SDK·언어 무관 스킬 모두 가능).
6. **벡터/RAG 설계** — ⬜ 미정. 보완 벡터·GraphRAG 구성.
