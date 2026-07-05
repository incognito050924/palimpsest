# palimpsest 생성형 큐레이터 재검토 — 단일 whole-design

- work item: `wi_260705lxy` (reexam-design-synthesis)
- 상태: 설계 확정(locked). 소스 변경 0 — 이 문서는 설계+계획 산출물이다.
- 결정: **Candidate B — 격리 opt-in 생산자 + git 선(先)물질화 → 기존 멱등 inferred 로더 적재, content-verdict 외부 유지.**
- 근거 사슬: 연구(drift map) → 4역할 적대 검증(dialectic) → Synthesizer 판정(verdict=REVISE→B).

## 0. 이 문서가 답하는 질문

palimpsest는 정초 VISION에서 **생성형 큐레이터**(archivist 아님)로 선언됐다. 그러나 이후 슬라이스들이 쌓아 올린 `provider-free`(LLM 호출 0, 생성 전적 외부) 라인이 이 정체성을 조용히 뒤집었는가? 뒤집었다면 되돌려야 하는가, 되돌린다면 어디까지인가 — 결정론·hermetic 테스트·no-laundering·git-재구축이라는 실제로 얻은 것들을 깨지 않으면서.

각 절은 하나의 수용 기준(AC)에 대응한다.

| 절 | AC | 내용 |
|---|---|---|
| §1 Drift map | ac-1 | 정초 VISION ↔ 누적 provider-free 라인의 표류, provider-free가 보호하는 것(E1–E5) |
| §2 적대 검증/반증 로그 | ac-2 | 양방향(유지/회복) 대칭 반박 + 인정된 반론과 오라클 |
| §3 3후보 비교표 | ac-3 | A(유지)/B(격리 생산자)/C(전면 역전), E1–E4 대비 평가, 결정 문단이 특정 행 인용 |
| §4 대안 whole-design 전개 | ac-4 | 라이벌 후보 A를 설계 수준으로 실제 전개 → B가 이기는 이유 |
| §5 확정 설계 — Candidate B | ac-7 | 일관된 whole-design, 모든 방향 항목 결정(open 0) |
| §6 교차 충돌 해소 | ac-6 | (a) 생성 vs hermetic/no-laundering/git-결정론 (b) 직접-CodeQL vs 전이력-backfill |
| §7 Provenance | ac-9 | 차용/기각/soft, 정초 baseline + 이번 설계의 새 선택 |
| §8 거버넌스 & change_condition 포인터 | ac-8 | supersede ADR 필요, hard-invariant 완화는 사용자 소유 |
| §9 열린 검증 공백 | ac-9 | VG1·VG2 — 이월된 검증 과제(설계 미결이 아님) |

---

## §1. Drift map — 정초 의도 ↔ 누적 provider-free (ac-1)

### 1.1 정초 의도 — 생성형이 1급 [VISION.md]

- **F1** 큐레이터(아카이비스트 아님): 조합형 + 생성형 근거결박 합성. `VISION.md:20-22`
- **F2** 기능 #4 Curate — 두 모드 모두 1급, 생성형은 출처 + gap + confidence로 사실분리(세탁 방지). `VISION.md:36`
- **F3** **잠긴 결정 #6(재론 아님, 결정적)**: 조합형 + 생성형 둘 다 1급. palimpsest는 standalone이라 ditto의 INFERRED-only/advisory 제약에 **구속되지 않는다**. 생성 제약은 오직 form-separation(세탁 금지)이며 **생성 자체 금지가 아니다**. `VISION.md:51`
- **F4** 3목표: 근거 KB로 할루시네이션 최소화 · context-rot 회피 · 의도 투명. `VISION.md:25-27`
- **F5** git = SoT(그래프 = 재구축 read-model). `VISION.md:47,33`
- **F6** standalone(ditto 밖). `VISION.md:46`

### 1.2 누적 provider-free 잠금 [ADR]

- **B1** provider-free = LLM 호출 0, 생성 전적 외부. `ADR-20260701-semantic-layer-load-contract:17`(hermetic rationale `:28`)
- **B2** 회상 경로 LLM-free 불변식(probe 강제). `:23`; v1 `:26`
- **B3** Risk/DesignDecision 판정·생성 외부, palimpsest는 적재·형식강제만. `ADR-20260702-risk-designdecision-load-contract:23`
- **B4** **provider-free = HARD INVARIANT, 완화는 범위 밖(별도 결정)**. `ADR-20260702-risk...:92` ← governing decision(ADR-0020) 대상. 생성 회복 설계와 정면 충돌하므로 반드시 표면화해야 한다.
- **B5** 임베딩도 외부 생산, `query_vector`도 호출자 공급. `ADR-20260704-semantic-embedding-load-contract:21,25,32`
- **B6** 외부 판정 ingest(생산 아님), content-verdict 외부. `ADR-20260701...:33`

### 1.3 Drift 표

| # | VISION 의도 | ADR 잠금 | Gap |
|---|---|---|---|
| D1 | 생성형 합성 1급(핵심 능력) `VISION:36,51` | palimpsest 생성 0, 전적 외부 `701:17`/`702:23` | as-built = 외부 추론의 아카이비스트/로더. 생성형 반쪽이 제품 경계 밖 |
| D2 | standalone ⇒ advisory/INFERRED-only에 **구속 안 됨**(생성 자유 확대) `VISION:51` | provider-free = ditto보다 **더** 제약(ditto 내부 큐레이터는 INFERRED 생산은 함, palimpsest는 0) `701:17`/`702:92` | **역방향 drift**: VISION은 standalone으로 생성 자유를 정당화했는데, ADR은 인접 논리(hermetic·외부분업)로 오히려 더 적은 생성능력을 잠갔다. VISION이 명명한 유일 제약(form-separation)은 지켜·강화됐고, VISION이 준 자유(생성)는 버려졌다 |
| D3 | 생성 제약 = **form**(출처+gap+confidence) `36,51` | 생성 제약 = **존재**(in-process 생성 자체 불가) `701:17,23`/`704:21` | location 제약(외부여야)이 form 제약(라벨링)을 대체. form 기계(`edge_kind=inferred`·grounding·`semantic_verdict`·confidence)는 실제로 구축됐으나 *ingested* 추론에만 부착되고 *produced*에는 없음 |
| D4 | 목표 #1: palimpsest가 근거결박 생성으로 할루시네이션 최소화 `25` | 외부출력 form 강제(grounding/atomic-reject)로 재프레임, palimpsest는 생성 안 함 `701:19,27`/`702:78` | 같은 목표, 반대 분업: VISION = palimpsest가 근거 생성, ADR = 남의 생성을 police |
| D5 | hard invariant status 없음, 생성형은 affirmatively lock-**IN** `51` | provider-free 자체를 hard invariant로 격상, 완화 범위 밖 `702:92` | VISION이 lock-IN한 능력(생성 1급)을 구현 stance가 lock으로 가림. 생성 회복 = B4 재개 필요(어떤 slice ADR도 재개하지 않음) |

### 1.4 화해/비충돌 — 문은 열려 있다

- **R1** v1 유예는 "deferred, not forbidden": `ADR-20260701-v1-ontology-recall-reframe:19` + `semantic:11`("유예는 금지 아님, edge_kind 분리 규약이 선인가"). `704:27`도 echo.
- **R2** **정초 ADR은 생성형 합성을 포함한다**: "회상·합성 = GraphRAG … LLM 합성으로 근거결박 답" `ADR-20260626-foundational-architecture:19` + form-separation verbatim `:27` → 정초 아키텍처는 생성 회복과 **정합**. provider-free는 downstream slice 선택이지 정초 mandate가 아니다.
- **R3** hard-invariant ADR의 change_condition이 재개 경로: `704:40`("query-vector 생산 어댑터 추가 시 provider-free 경계 재검토") → 완화는 명명된 비금지 트리거를 이미 갖는다(단 B4가 별도 ADR로 결정을 위임).
- **R4** **inferred-layer 골격이 이미 존재하고 provider-free로 라벨돼 있다 → 생성기 꽂기는 additive**: `edge_kind=inferred` 파티션 · grounding/atomic-reject · generator/model/`semantic_verdict` · 분리 채널(summaries/risks/decisions/relations) 모두 구축됨. `701:20-22`/`702:27,33,35`. 생성 회복 = 현재-외부 생산자 공급 자리에 넣는 것이지 load contract 재작성이 아니다.

### 1.5 provider-free가 보호하는 것 (완화 비용)

| # | 보호 대상 | 근거 | 완화 시 위협 |
|---|---|---|---|
| E1 | hermetic 테스트(키·네트워크 0 재현) | `701:17,28`/`704:25` | 테스트가 네트워크/키/LLM 의존 또는 mock 경계를 획득 |
| E2 | no-laundering(deterministic vs inferred) — 전용 inferred 로더가 deterministic ingest 경로 재사용 금지 → 판정을 구조사실로 세탁 방지 | `702:29,77,81`/`701:20` | **이건 VISION 자신의 no-laundering 규칙(F2/F3) 구현이므로 생성자와 무관하게 유지돼야 한다** |
| E3 | git-determinism/rebuild — 결정적 `summary:`/`risk:`/`decision:` sha256 id + MERGE 멱등 → Neo4j drop→reload 동일 재현, git-tracked SoT | `701:34`/`702:25` | in-process 비결정 LLM 생성기는 출력이 git-persist 안 되면 rebuild-determinism 위협 |
| E4 | 외부-판정 분업 — 판정을 외부 생산자에 할당, palimpsest는 ingest+form | `702:23,79`/`701:33` | **VISION #6이 문제 삼은 바로 그 분업**(VISION: standalone은 외부 advisor 의존에서 해방인데, ADR은 모든 판정/생성에 외부의존을 재도입). 단, 생성자≠검증자 정직성은 별도 가치로 남는다(§2 참조) |
| E5 | 임베딩 공간 정합성 + 정직한 staleness — single-model/dim-pin, 재인코딩 외부 | `704:18,32` | 임베딩 축은 이번 회복 범위 밖(§5.5 참조) |

### 1.6 Governing flag (ADR-0020)

`ADR-20260702-risk...:92`가 provider-free를 hard invariant로 선언하고 완화를 별도 결정으로 유예한다 → 생성형 큐레이션 회복 설계는 **intent-level 충돌**이며, basis와 함께 반드시 surface해야 한다(§4-10). 반대추(R2/R3): 정초 `ADR-20260626:19,27`은 생성을 포함하고 유예는 금지가 아니다 → 충돌은 **slice-level hard invariant**이지 정초 아키텍처가 아니다.

### 1.7 불확실성

- 어떤 ADR도 in-process 생성을 영구 *금지*하진 않는다(최강 잠금 `702:92`도 유예 + hard invariant). "영구 금지 없음"은 5개 ADR 부재를 근거로 한 판단이며 전수 grep이 아니다. [hypothesis → VG1]
- 10개 중 5개 ADR만 정독했다(backfill·community-deterministic·communityreport·decision-lineage·branch-scoped 미정독). baseline은 이들이 provider-free/생성 축을 미접촉으로 요약하나 텍스트 미검증. [→ VG1]

---

## §2. 적대 검증 / 반증 로그 (ac-2)

명제와 반명제를 **대칭적으로** 방어했다. 4역할(P=Proponent 양방향, O=Opponent 양방향)을 fresh-context에서 코드 검증과 함께 수행했다.

### 2.1 P1 · Producer = 회복 (생성 회복 최강 논거)

- provider-free는 정초 mandate가 아니라 slice 4의 선택이다(`ADR-20260701:6`). VISION #6은 생성형 1급을 "재론 아님"으로 잠갔고(`VISION:51`), 유일 제약은 form-separation(location 아님, `VISION:36`).
- 정초 `ADR-20260626:19,27`이 LLM 합성을 포함 → 회복은 VISION 위반이 아니라 복원.
- **결정적 코드 발견**: provider-free probe는 전역이 아니라 **경로-스코프**다 — `tests/recall/test_recall.py:117-145`가 `palimpsest.recall` import 시 생성 모듈 0을 검사한다(전역 금지가 아님).
- **제안**: 격리 생산자 모듈(`palimpsest.curate`) + 별도 CLI가 git-tracked `summaries/` SoT에 payload를 먼저 물질화 → 기존 멱등 로더(`src/palimpsest/kg/summary.py:308`)로 적재. E1 유지(recall/load probe 무저촉), E2 강화(inferred 로더 통과), E3 유지(git 동결 후 rebuild), 불변식 축소: "어디서도 LLM 0" → "recall·load 경로 LLM-free + 격리 opt-in 생산자". = `ADR-20260704:40(c)` 명명 트리거의 실현.
- **못 답한 반론(회복측이 인정)**: 생성자=검증자 분리(E4). 로더는 form(ref resolve)만 검사하고 content(판정이 참인지)는 검사하지 않는다(`ADR-20260701:33`이 content-verdict 외부 유예). in-process 생성은 자기-인증 우려를 재도입하며 form-check로는 닫히지 않는다.

### 2.2 P2 · Producer = 유지 (provider-free 유지 최강 논거)

- provider-free = 생성 폐기가 아니라 "대체 가능한 반쪽(생성)을 밀어내고 희소·방어 가능한 반쪽(grounding 기층·form 강제)을 소유". 결정론 경계 = 신뢰 경계.
- E1 hermetic은 유효성 조건(`tests/recall/test_recall_semantic.py:207-233` probe). E2 no-laundering은 코드 강제(`tests/kg/test_risk.py:160-199`: det ⊎ inferred == total ∧ NULL == 0). E3 git-determinism(sha256 + MERGE 멱등).
- in-process 생성은 durability 축에서 얻는 것이 없고 결정론만 잃는다(payload를 git화하면 그건 곧 현행 외부모델과 같아진다).
- **못 답한 반론(유지측이 인정)**: 역방향 drift(D2/D5) — "provider-free가 VISION이 lock-IN한 능력을 조용히 lock-OUT으로 뒤집는다". "deferred, not forbidden"은 텍스트상 참이나 회복 슬라이스가 0이라 실무적으로 lock-OUT과 구별되지 않는다. 생성이 실제로 회복될 증거를 대지 못한다.

### 2.3 O-keep · Opponent = 유지 방어 (회복 반박)

- **OBJ-1 CRITICAL**: `§92` hard invariant + `§4-10` → intent 충돌은 사용자 ADR로만 해소. 무단 실행은 fatal.
- **OBJ-2 CRITICAL**: hermetic probe가 5경로(`tests/kg/test_summary.py:384-409` 등) — 격리로 mitigable하나 그러면 "여전히 provider-free core".
- **OBJ-3 HIGH**: 비결정 LLM → 다른 sha256 → drop/reload 재현 실패. **payload를 git-SoT로 영속화하면 mitigable(= 그 형태가 곧 provider-free 외부모델)**.
- **OBJ-4 MEDIUM 양보**: no-laundering 형식층은 **생성자 중립 → 회복의 결정적 반대가 아님**. 잔여 = provenance 자기귀속 정직성(generator/model이 palimpsest 자기 자신을 가리키지 않게).
- **OBJ-5 MEDIUM**: 임베딩 단일-model 불변식(704)은 제외/어댑터 한정.
- **명명한 중간안**: ① 외부 produce → git → load(현행) ② 하드 모듈 경계 뒤 어댑터(`704:40c`) ③ supersede ADR + 사용자 결정.
- **종합**: 반박의 무게는 "생성 금지"가 아니라 **"in-process core 침투 금지, hard invariant 완화는 사용자 ADR로만"**. `VISION:51`은 회복 쪽 오라클을 지지함을 인정.

### 2.4 O-reclaim · Opponent = 회복 방어 (유지 반박)

- **O1 CRITICAL**: 영구유지 = VISION 잠긴 결정 #6 위배(slice가 비재론 능력을 우회 폐기). 유예해석이라면 downgrade.
- **O2 CRITICAL**: 역방향 drift — 생성 자유를 준 전제(standalone)로 그 자유를 뺏는 근거는 자기모순.
- **O3 HIGH**: 정초 `ADR-20260626:19,27,34`가 LLM 합성 포함 → 영구 제거는 비전 차원 변경인데 slice가 월권.
- **O4 HIGH**: 자기완결성 훼손 — 흡수 대상(ditto)에 생성/판정을 역의존(`VISION:50`). 최소 1개 핵심 능력(요약 합성)에 in-process 경로 확보 필요.
- **O5 MEDIUM**: "hard invariant"가 lock을 과장 — `702:92`는 별도 결정 유예(금지 아님), `704:40(c)` 재개 트리거 존재.
- **additive 경로 A1–A3**: 경계 뒤 어댑터 / git-persist 생성 출력 / form-separation 유지·location만 완화(load contract 재작성 아님, R4).
- **SC1 scope-creep 경고**: 회복 = 생성형 합성(요약) 1급이지 판정·임베딩 전부의 in-process화가 아니다. E2(세탁 금지)·판정 외부는 별도 판단으로 남길 것.

### 2.5 수렴 — 양측 공통 지반

1. no-laundering(E2)은 생성자 중립 — 양 Opponent 합의, 회복의 blocker가 아니다.
2. E1·E3는 "격리 생산자 + git 먼저 물질화 → 멱등 로더" 패턴으로 보존(양측이 명명: O-keep 중간안 ②, O-reclaim A1–A3).
3. E4(생성자≠검증자)는 content-verdict를 외부 유지(`ADR-20260701:33` 현행)하면 보존 → reclaim측 최강 반론 해소.
4. VISION #6·정초 `ADR-20260626 §2`는 생성 회복 쪽 오라클. provider-free 영구유지는 정초 §2 초과폐기(월권).
5. 거버넌스: 완화는 `§92` hard invariant supersede → 새 ADR + 사용자 결정 필수. **사용자가 deep-interview에서 이미 승인**(전 스펙트럼 열림 확인, 착수 지시).

---

## §3. 3후보 비교표 + 결정 (ac-3)

각 후보를 최소 1 PRO + 1 CON + 오라클과 함께, 그리고 E1–E4 대비로 평가한다.

| 후보 | PRO (+oracle) | CON (+oracle) | E1 | E2 | E3 | E4 | VISION #6 / §2 |
|---|---|---|---|---|---|---|---|
| **A** 현행유지(provider-free 영구) | 신뢰 = 결정론 경계 완전 보존 (`701:17,28`; `702:25`) | 역방향 drift, 생성형 1급 lock-OUT, 정초 §2 초과폐기 (`VISION:51`; `626:19,27`); 무기한 유예 = 실무 lock-OUT(P2 미답) | ✅ | ✅ | ✅ | ✅ | ❌ 위배 |
| **B** 격리 opt-in · git 선물질화 → 멱등 로더 · verdict 외부 | VISION #6 합성 1급 복원(`VISION:36,51`) + 4속성 전부 보존; probe가 경로스코프라 격리로 green(`test_recall.py:117-145`); inferred 로더 재사용 = additive(`summary.py:308`, R4); `704:40c` 재개 트리거 실현 | governance ADR 필수(`702:92` supersede + 사용자 결정); VG2 rebuild-det 미검증; provenance 자기귀속 정직성 잔여(OBJ-4) | ✅ | ✅ | ✅ | ✅ | ✅ 정합 |
| **C** 전면 역전(생성+판정+임베딩 내부) | 최대 자기완결(`VISION:46,50`) | E4 파괴 = 자기인증(P1 미답 재현, `701:33`); hermetic 붕괴(probe 5경로); SC1 범위 초과 | ❌ | ⚠️ | ❌ | ❌ | ⚠️ 초과 |

### 3.1 결정 = Candidate B

Synthesizer 판정: verdict **REVISE → Candidate B**. 명제("생성형 큐레이션 1급 회복")를 **방향은 채택하되 범위를 축소**한다.

결정 근거는 위 표의 **특정 행**에 결박한다:

- **B 행이 E1–E4를 전부 ✅로 가지는 유일한 행이다.** A 행은 VISION #6/§2 열에서 ❌(위배), C 행은 E1/E3/E4에서 ❌(파괴). 따라서 "생성 회복"과 "얻은 것 보존"을 동시에 만족하는 후보는 B뿐이다.
- **A 행의 CON**("무기한 유예 = 실무 lock-OUT")은 유지측(P2)이 못 답한 반론인데, **B 행의 PRO**("실제 회복 슬라이스 출하")가 이를 해소한다.
- **C 행의 CON**("E4 파괴 = 자기인증")은 회복측(P1)이 못 답한 반론인데, **B 행의 PRO/설계**("content-verdict 외부 유지, `701:33`")가 이를 해소한다. C는 이 반론을 재현하므로 기각.
- 따라서 A 기각(VISION #6·정초 §2 초과폐기 + 역방향 drift), C 기각(E4 파괴 + SC1 초과), **B 채택**.

### 3.2 B가 함의하는 required_edits

1. 격리 생산자 모듈 + 별도 CLI: recall/load import surface 밖에서 payload를 git-SoT에 **먼저** 물질화(OBJ-2).
2. 물질화 payload를 기존 멱등 inferred 로더로 적재, deterministic ingest 경로 재사용 금지(OBJ-3 · E2).
3. content-verdict 외부 유지, 판정·임베딩 in-process화 금지(E4 · SC1, C 배제).
4. provenance 자기귀속 정직성: generator/model이 palimpsest 자기 자신을 가리키지 않게(OBJ-4).

---

## §4. 대안 whole-design을 설계 수준으로 전개 (ac-4)

라이벌로 **Candidate A**를 고른다. 단순 "현행 동결"이 아니라, A를 가장 강한 형태 — "provider-free를 지키면서 drift를 닫는" 진지한 설계 — 로 전개한 뒤 B와 정면 비교한다. (표의 한 행이 아니라, 실제 모듈 경계·데이터 흐름·산출물까지 내려간 설계다.)

### 4.1 Candidate A′ — "provider-free 유지 + 외부 생산자 파이프라인 출하로 drift 닫기"

**설계 명제**: 생성 능력을 palimpsest 안에 넣지 않는다. 대신 (1) 이미 구축된 inferred-layer 골격을 "실현된 생성형 반쪽"으로 공식 선언하고, (2) palimpsest 밖에 사는 외부 생산자 파이프라인을 1급 산출물로 출하하여, D1/D5의 drift를 "제품 경계 밖이지만 출하된 생성"으로 닫는다.

**모듈 경계 (A′)**:

```
[외부 저장소/도구]                    [palimpsest — provider-free core 불변]
  ext-producer (별도 repo/패키지)        summaries/  risks/  decisions/   (git-tracked SoT, 현행)
    - LLM 호출: 여기서만                    │
    - grounding/gap/confidence 부착         ▼
    - payload → git PR/커밋 ─────────▶  kg/summary.py 등 멱등 inferred 로더 (현행, 무변경)
                                            ▼
                                          Neo4j read-model (edge_kind=inferred)
                                            ▼
                                          recall (LLM-free probe 유지)
```

**데이터 흐름 (A′)**: 외부 생산자가 KB를 읽어 요약/판정을 LLM으로 생성 → `summaries/*.json` 등에 grounding + gap + confidence를 담아 물질화 → 그 파일을 palimpsest 저장소에 커밋(사람 리뷰/PR) → 기존 로더가 적재. palimpsest 코드는 **한 줄도 안 바뀐다**.

**A′가 실제로 출하하는 것**: (i) 외부 생산자 참조 구현(별도 패키지), (ii) payload JSON 스키마 문서, (iii) inferred-layer를 "실현된 생성형 반쪽"으로 선언하는 ADR. provider-free는 문자 그대로 유지된다(palimpsest 안 LLM 0).

### 4.2 A′는 왜 매력적인가 (정직한 강점)

- E1–E4를 **글자 그대로** 전부 보존한다. hermetic probe도 손댈 필요 없다(생산자가 애초에 palimpsest 밖).
- supersede ADR이 "선언"에 그쳐도 된다 — 불변식 텍스트를 완화할 필요가 없어 거버넌스 마찰이 최소.
- R4가 지적한 대로 골격이 이미 있으니, 정말로 코드 변경 0으로 "생성형 반쪽이 존재한다"고 말할 수 있다.

### 4.3 왜 B가 A′를 이기는가 (구체 대결)

| 축 | A′ (외부 생산자 출하) | B (격리 opt-in 생산자) | 판정 |
|---|---|---|---|
| VISION #6 "생성형이 palimpsest의 1급 능력" | 생성은 여전히 **제품 경계 밖**. palimpsest는 "남이 만든 걸 적재"하는 아카이비스트로 남음 → **D1 drift가 닫히지 않고 위치만 이동** | 생성이 palimpsest 배포물 안의 opt-in 능력(`palimpsest.curate` + CLI). "palimpsest가 근거결박 합성을 한다"가 문자 그대로 참 | **B** — A′는 D1을 재라벨할 뿐 |
| F6 standalone 자기완결(`VISION:46,50`) | 생성을 쓰려면 **외부 생산자에 역의존**. VISION #6이 문제 삼은 바로 그 외부-advisor 의존(E4 표의 지적)을 구조로 고착 | 최소 1개 핵심 능력(요약 합성)이 in-process. 외부 도구 없이도 생성형 큐레이션이 성립 | **B** — O4 반론이 A′를 정확히 관통 |
| D2 역방향 drift(자유를 준 전제로 자유를 뺏음) | 완화 없이 유지 → 역방향 drift 그대로. "출하했다"는 서사만 추가 | 불변식을 "recall+load LLM-free + 격리 생산자 허용"으로 정련 → drift 자체를 봉합 | **B** |
| E1–E4 보존 | ✅ 전부(무변경) | ✅ 전부(격리 + git 선물질화 + verdict 외부) | 무승부 |
| 거버넌스 비용 | 낮음(선언 ADR) | 중간(불변식 정련 supersede ADR + 사용자 결정) | A′ 우위지만, 사용자가 이미 완화를 승인(§8) → 이 우위는 소진됨 |
| 실무적 lock-OUT 반론(P2 미답) | **미해소** — 생성이 여전히 palimpsest 밖이라 "제품이 생성형 큐레이터"라는 주장은 계속 공허 | 해소 — opt-in이지만 palimpsest 배포물이 생성 경로를 소유 | **B** |

**결론**: A′는 E1–E4 보존과 거버넌스 저비용에서 강하지만, **정확히 핵심 명제(VISION #6: 생성형이 palimpsest의 1급 능력)를 만족시키지 못한다.** A′의 생성은 영원히 남의 것이고, palimpsest는 아카이비스트로 남는다 — 이는 D1 drift를 닫는 게 아니라 위치만 옮긴 것이다. 게다가 A′는 standalone 자기완결(F6/O4)을 외부 역의존으로 훼손한다. B는 E1–E4를 똑같이 보존하면서 명제를 실제로 만족시키므로, 거버넌스 비용(사용자가 이미 지불)을 제외한 모든 결정 축에서 A′를 이긴다.

---

## §5. 확정 설계 — Candidate B (ac-7)

일관된 whole-design이다. **모든 in-scope 방향 항목이 결정됐다 — open/decide-later 0.** (VG1/VG2는 검증 과제이지 설계 미결이 아니다 — §9.)

### 5.1 한 문장 요약

palimpsest 배포물 안에 **격리된 opt-in in-process 생산자**(`palimpsest.curate`)를 두되, 그 출력은 **별도 CLI가 git-tracked SoT(`summaries/` 등)에 먼저 물질화**한 뒤 **기존 멱등 inferred 로더**(`kg/summary.py` 등)로 적재한다. content-verdict는 외부 유지한다. provider-free 불변식은 "어디서도 LLM 0"에서 **"recall+load 경로 LLM-free + 격리 opt-in 생산자 허용"**으로 좁힌다.

### 5.2 모듈 경계 (B)

```
palimpsest/                              불변식 상 위치
├── curate/            ← 신설. 격리 opt-in 생산자.  [LLM 허용 구역]
│     - KB/코드 읽어 요약 payload 생성(grounding+gap+confidence)
│     - recall/load 를 import 하지 않음 (경로-스코프 probe 밖)
│     - content-verdict(판정 참/거짓)는 생성하지 않음 → 외부
├── cli (curate 서브커맨드)  ← 신설.        [물질화 단계]
│     - curate 호출 → payload 를 summaries/*.json 등 git-SoT 에 write
│     - 여기서 커밋(사람 리뷰 가능) — git 이 결정론 경계
├── kg/summary.py 등 inferred 로더  ← 무변경.  [LLM-free load 경로]
│     - git-SoT payload 를 읽어 sha256 id + MERGE 멱등 적재
│     - edge_kind=inferred, deterministic ingest 재사용 금지(E2)
└── recall/           ← 무변경.              [LLM-free recall 경로]
      - probe(test_recall.py:117-145)가 생성모듈 0 을 계속 강제
      - curate 는 recall 의존 그래프에 없음 → probe green 유지
```

핵심 불변식 배치: **경로-스코프 probe**(`tests/recall/test_recall.py:117-145`, `tests/recall/test_recall_semantic.py:207-233`, `tests/kg/test_summary.py:384-409` 계열 5경로)가 검사하는 것은 `palimpsest.recall`/load surface의 import 폐포다. `palimpsest.curate`가 이 폐포 밖에 있는 한 probe는 green이다 — 이것이 "격리"의 기계적 정의다.

### 5.3 데이터 흐름 (B) — 성공 경로 1개 추적

1. 사용자가 opt-in으로 `palimpsest curate summaries` 실행(기본 파이프라인에는 없음).
2. `palimpsest.curate`가 대상 노드/코드를 읽어 요약을 LLM으로 생성, grounding refs + gap + confidence를 부착한 payload 산출. **판정(이 요약이 참인가)은 만들지 않는다.**
3. CLI가 payload를 `summaries/<deterministic-id>.json`으로 **git에 물질화**. 이 시점부터 출력은 결정론적 파일이다(비결정 LLM은 여기서 동결).
4. (기존 흐름) `kg/summary.py:308`의 멱등 로더가 payload를 읽어 `summary:` sha256 id + MERGE로 적재, `edge_kind=inferred`, grounding 엣지 부착. deterministic ingest 경로는 건드리지 않는다.
5. Neo4j drop → 3단계의 git-SoT에서 reload → **동일 그래프 재현**(E3; 실행 검증은 VG2로 이월).
6. recall은 평소처럼 LLM-free로 회상. probe green.

### 5.4 불변식 정련 (narrowing)

| 항목 | 기존(as-built) | Candidate B |
|---|---|---|
| provider-free 정의 | 어디서도 LLM 0(`701:17`) | recall+load 경로 LLM-free **+** 격리 opt-in 생산자 허용 |
| 생성 위치 | 전적 외부(`701:17`, `702:23`) | 격리 in-process(`palimpsest.curate`) **또는** 외부 — 둘 다 payload를 git-SoT로 물질화 후 로더 통과 |
| content-verdict | 외부(`701:33`) | **외부 유지(무변경)** — E4 보존, C와의 결정적 차이 |
| no-laundering | inferred 로더 전용, det 재사용 금지(`702:29,77,81`) | **무변경** — 생성자 중립(E2), 강화됨 |
| git-SoT 결정론 | sha256 + MERGE 멱등(`701:34`) | **무변경** — 생성 출력도 git 동결 후 로더 통과 |

### 5.5 명시적 범위 경계 (in-scope 결정)

- **임베딩 축**: 회복 범위 **밖**. 임베딩은 외부 single-model 유지(`704:18,32`, `704 §1` 무저촉). ADR에 분리 명시. — 결정됨.
- **판정(content-verdict)**: 외부 유지. in-process화 금지. — 결정됨(E4).
- **provenance 자기귀속**: `generator`/`model` 필드가 palimpsest 자기 자신을 가리키지 않도록 정직 표기 규약 채택(OBJ-4). curate가 만든 payload도 실제 생성 모델을 기록. — 결정됨.
- **opt-in 여부**: 기본 파이프라인(backfill/reconcile)에 포함하지 않는다. 명시적 서브커맨드로만. — 결정됨.

이 4개가 in-scope 방향 전부이며 모두 결정됐다. 남은 것은 검증 과제(VG1/VG2)뿐이다.

---

## §6. 교차 충돌 해소 (ac-6)

### 6.1 (a) 생성형 큐레이션 vs hermetic-tests / no-laundering / git-determinism

충돌 주장: "in-process 생성을 허용하면 E1(hermetic)·E2(no-laundering)·E3(git-결정론)·E4(생성자≠검증자)가 깨진다." B의 3중 장치가 각각을 어떻게 보존하는지:

| 보호 대상 | 위협 | B의 장치 | 왜 보존되는가 |
|---|---|---|---|
| **E1 hermetic** | curate가 LLM/네트워크 의존을 테스트 경로에 끌어들임 | **path-scoped probe + 격리** — curate는 recall/load import 폐포 밖 | probe(5경로)가 검사하는 surface에 curate가 없다. hermetic 테스트는 recall/load만 실행하므로 키·네트워크 0 재현 유지. curate는 opt-in이라 CI 기본 경로에 없음 |
| **E2 no-laundering** | 생성 판정을 구조사실로 세탁 | **기존 inferred 로더 재사용, deterministic ingest 경로 금지** | curate payload도 `edge_kind=inferred`로만 적재. det ⊎ inferred == total ∧ NULL == 0 불변식(`test_risk.py:160-199`) 무변경. E2는 생성자 중립이므로 curate 유무와 무관하게 성립(2.5 수렴 #1) |
| **E3 git-determinism** | 비결정 LLM → 다른 sha256 → drop/reload 재현 실패 | **git-materialize-first** — CLI가 payload를 git-SoT에 먼저 동결, 로더는 그 파일만 봄 | 비결정성은 git 커밋 시점에 흡수된다. 로더 입력이 결정론적 파일이므로 sha256 id + MERGE 멱등 → drop→reload 동일(현행 외부모델과 동형). 실행 확인은 VG2 |
| **E4 생성자≠검증자** | in-process 생성이 자기-인증(판정을 스스로 만들고 스스로 적재) | **content-verdict 외부 유지** — curate는 요약(합성)만, 판정은 안 만듦 | 로더는 form(ref resolve)만 검사하고 판정 참/거짓은 검사하지 않는다(`701:33` 무변경). 판정 생산이 외부에 남으므로 자기-인증 고리가 닫히지 않는다. 이것이 C(전면 역전)와 B의 결정적 분기 |

요지: **git-materialize-first(E3) + path-scoped probe(E1) + external-verdict(E4)** 3중 장치가 E1/E2/E3/E4를 모두 보존한다. E2는 애초에 생성자 중립이라 보너스로 유지된다.

### 6.2 (b) 직접-CodeQL-실행 vs 전이력-backfill 균일성 (명시적 해소)

**충돌**(design-notes §C.2/C.3, `design-notes.md:200-211`): 사용자 soft 선호는 "palimpsest가 CodeQL을 **직접** 실행"(`C-Q2`, `design-notes.md:235`). 그러나 CodeQL 정확도는 **빌드/컴파일 추적**에 의존한다 — 옛 커밋은 빌드가 안 되고(`:201`), 빌드 환경 의존이라 git만으로 재구축이 안 닫히며(`:202`), per-commit DB 빌드는 비현실적(`:203`). 이는 palimpsest의 전이력 backfill(`git archive` 트리 파싱, 빌드리스) 불변식과 **정면 충돌**한다.

**결정 — 정밀도의 주경로는 palimpsest가 소유하는 build-less tree-sitter spine이고, CodeQL은 선택적 보조 overlay다** (사용자 결정 "A + CodeQL 보조"):

1. **주(PRIMARY) = 소유한 build-less tree-sitter 정밀 spine**. 현행 name-based over-matching(`src/palimpsest/extract/java.py` — `CALLS`가 호출을 그 단순명을 가진 **모든** 메서드에 연결)을 in-house로 개선한다: tree-sitter `tags.scm` + `locals` 스코프 해소 + receiver-type 휴리스틱으로 좁힌다. 다언어·전이력 균일(임의 git 체크아웃, 빌드 불요)·palimpsest 소유이며, test-impact 필요(design-notes D §0 pain)를 푸는 1급 경로다. CodeQL은 이 spine의 의존이 **아니다**.
2. **보조(OPTIONAL AUXILIARY) = CodeQL HEAD-only overlay**. 빌드 가능한 HEAD에서만 실행하며 ① 정밀 부스트(virtual dispatch, dataflow) ② security/taint Risk를 더한다 — build-less가 대체 못 하는 **유일한 역할이 interprocedural taint(보안 Risk)**다. 지금은 드롭 가능하고, 결정론적 security-Risk 생산자가 실제로 필요해질 때 채택한다. 옛 커밋 빌드는 시도하지 않는다.
3. **CodeQL도 같은 격리-생산자 / git-materialize 패턴**(§5의 B 패턴): findings를 git-SoT에 물질화 → 기존 inferred/deterministic 로더로 적재. 비결정/환경의존 단계를 git 커밋에 흡수, 로더 입력은 결정론적 파일.
4. **정직한 coverage-asymmetry provenance**: "HEAD-only 정밀 CodeQL 엣지" vs "전이력 tree-sitter 엣지"를 provenance로 구분(`design-notes.md:210`, `C-Q3`/`C-Q6`). 회상은 CodeQL findings가 이력을 안 덮음을 정직하게 표기.
5. **직접 실행 vs DITTO 소비**(`C-Q2`)는 CodeQL 보조를 채택할 때의 자유도로 남긴다: 어느 쪽이든 산출물이 "HEAD findings → git 물질화 → 로더"를 통과하면 불변식은 동일하게 보존된다.

**연구 근거 — 왜 정밀 spine을 소유해야 하나 (2026 웹 조사)**: build-less + 다언어 + 정밀 콜그래프를 동시에 만족하는 성숙한 off-the-shelf 도구는 **없다**(트릴레마). 따라서 build-less 정밀은 마법 도구에서 조달하는 게 아니라 소유(tree-sitter 개선)해야 한다.
- 정밀 도구는 전부 **빌드 필요**(전이력 균일성 위배): SCIP indexer(scip-java/python/typescript, https://sourcegraph.com/docs/code-search/code-navigation/precise_code_navigation), LSP 서버, Kythe, Glean.
- build-less 다언어 도구는 **구문·단일 파일 한정**(콜그래프 없음): tree-sitter `tags.scm`/`locals`(로컬 스코프만, https://tree-sitter.github.io/tree-sitter/4-code-navigation.html), ast-grep(https://ast-grep.github.io/).
- **stack-graphs**(build-less cross-file 후보)는 **2025-09-09 아카이브**(read-only; 마지막 릴리스 2024-12-13; Kotlin 미지원; pre-1.0 룰셋), https://github.com/github/stack-graphs.
- **sound한 정적 콜그래프는 없다** — CodeQL조차 reflection/DI/dynamic-dispatch 엣지를 놓친다(ISSTA 2024 "Total Recall? How Good Are Static Call Graphs Really?", https://dl.acm.org/doi/10.1145/3650212.3652114). test-impact의 유일한 ground truth는 런타임 coverage(build+run, HEAD-only)다.

**CodeQL Risk의 no-laundering 정합**(`C-Q5`, `design-notes.md:228-229`): CodeQL risk는 도구-유래 결정론(빌드 있으면 규칙 재현)이라 순수 deterministic과 LLM-inferred 사이에 있다. `extracted_by:'codeql'` provenance 마커로 "누가 찾았나"를 박아 세탁 금지 불변식과 정합시킨다 — 세부 edge_kind 표기 규칙은 CodeQL 슬라이스에서 확정(이 문서 범위 밖, design-notes에 개방 질문으로 이미 존재).

요지: **정밀 spine은 palimpsest가 소유하는 build-less tree-sitter 개선이 주경로**이고(다언어·전이력 균일), CodeQL은 정밀 부스트 + 대체 불가 니치(보안 Risk)를 위한 선택적 HEAD-only 보조다. build-dependency는 CodeQL 보조에만 있고 HEAD-only로 격리되며, 전이력 균일성은 build-less tree-sitter spine이 담보하므로 충돌이 해소된다.

---

## §7. Provenance (ac-9)

정초 baseline(borrowed/rejected)과 **이번 설계의 새 선택**을 함께 기록한다.

### 7.1 BORROWED / ADOPTED (정초 baseline)

- **CPG / Joern** → 정적층 온톨로지 출발점(type-labeled 노드셋 + 다중 엣지-타입 overlay). `docs/research/precompute-hugrag-kg.md:58-66`; `.ditto/knowledge/DESIGN.md:37,39`
- **Meta Glean (+Angle)** → "git=SoT, graph=재구축 projection, 사실 pre-compute 후 질의(재파싱 아님)", derived-fact → LLM-추출 semantic 엣지를 derived 층으로. "모델만 차용, 구현 아님(Glean=RocksDB+Angle)". `precompute-hugrag-kg.md:20-29,119`
- **HugRAG (CausalRAG2, arXiv 2602.05143)** → "unified edge space"(구조∪계층∪추론을 한 그래프에서 우선순위 순회), confidence-threshold 게이팅 → inferred 엣지 confidence 속성. "아이디어만 차용, 매핑은 자체 설계". `precompute-hugrag-kg.md:39-49`
- **Microsoft GraphRAG** → community/community-report 노드 개념(탐지 + LLM report prose). `precompute-hugrag-kg.md:88`; `VISION.md:57`
- **Graphiti(getZep)/Zep** → 2축 신선도 + 전이력 보존(bi-temporal validity, "invalidate instead of delete"), Episode=provenance(git commit 매핑). `precompute-hugrag-kg.md:68-75`; `ADR-20260702-decision-lineage-freshness.md:9`
- **DITTO CodeQL 산출** → 외부 정규화-IR producer로 load 소비(구조 팩트 + 보안/위험), provider-free 적재와 정합. `design-notes.md:194,211,225`
- (낮은 검증 tier §7) **Code Digital Twin (CDT, arXiv 2503.07967)**, **Meta "five questions"**, **Lore (arXiv 2603.15566)**, **Heidelberg "Context Engineering"(arXiv 2510.21413)** → semantic 노드 스키마·tacit 계보 후보. `precompute-hugrag-kg.md:139-165`

### 7.2 REJECTED / NOT ADOPTED (정초 baseline)

- **Postgres+pgvector+AGE** → 첫 빌드에서 기각(단일-DB 통합 최약, 네이티브 커뮤니티 탐지 없음, Python GraphRAG 툴링 최약). `db-substrate-spike.md:63,70`
- **CodeQL을 코어/이력-spine 엔진으로** → 기각(정확도가 빌드/컴파일 추적 의존 → 전이력 backfill·git-closed 재구축 불변식과 정면 충돌, per-commit DB 비현실적, 언어 taxonomy 제한). HEAD-only 정밀 overlay로 한정. `design-notes.md:200-205,207` — **§6.2 결정이 이 기각을 재확인**.
- **live를 노드 속성 저장** → 기각(파생상태 이중화 drift), read-time 파생. `ADR-20260702-decision-lineage-freshness.md:47`
- 온톨로지 엔티티 기각: Author 노드/EVOLVED_FROM/별도 Change 노드/풀 Branch 노드 → 흡수 또는 엣지화. `DESIGN.md:24-25`; `design-notes.md:304-309`

### 7.3 이 설계의 새 선택 — BORROWED / REJECTED / SOFT

- **[새 BORROWED] 격리-생산자 / git-materialize-first 패턴** → 원류는 정초의 "외부 produce → git → load"(현행)이고, 이번 설계는 이를 **생산자 위치를 격리 in-process까지 확장**한 형태로 재사용한다. `ADR-20260704:40(c)`의 명명된 재개 트리거를 실현. curate 축(§5)과 CodeQL 축(§6.2)이 **같은 패턴을 공유** — 새 추상화가 아니라 기존 로더 계약의 additive 사용(R4).
- **[새 REJECTED] Candidate C — 전면 역전(생성+판정+임베딩 in-process)** → 기각. E4 파괴(자기-인증, `701:33`), hermetic 붕괴(probe 5경로), SC1 범위 초과(§3 표 C 행). content-verdict를 in-process로 끌어들이는 순간 생성자≠검증자가 무너진다.
- **[새 REJECTED] Candidate A — provider-free 영구유지** → 기각. VISION #6·정초 §2 초과폐기, 역방향 drift, 무기한 유예=실무 lock-OUT(§3 A 행, §4의 A′ 전개). 강화형 A′조차 생성을 제품 경계 밖에 두어 명제를 만족 못 함(§4.3).
- **[유지 결정 = 새 REJECTED alternative] content-verdict in-process화** → 명시 기각, **외부 유지**. E4 보존이 B와 C를 가르는 결정적 선택(§5.4).
- **[SOFT / OPEN — CodeQL 축]** design-notes §C의 개방 질문은 이 문서가 §6.2에서 패턴을 확정하되 세부는 남긴다: `C-Q2`(직접 실행 vs DITTO 소비)는 패턴 하에서 둘 다 수용 가능으로 남김; `C-Q5`(도구-유래 결정론 Risk의 edge_kind 표기)는 CodeQL 슬라이스에서 확정; `C-Q1`(이력 spine 추출기 tree-sitter vs SCIP/stack-graphs)은 개방. `design-notes.md:234-239`

---

## §8. 거버넌스 & change_condition 포인터 (ac-8)

- **supersede ADR 필요(다음 노드가 저술)**: `ADR-20260701-semantic-layer-load-contract §1`을 **"어디서도 LLM 0" → "recall+load 경로 LLM-free + 격리 opt-in 생산자 허용"**으로 정련하는 supersede ADR을 발행해야 한다. `ADR-20260704 §1`(임베딩 single-model)은 B 미저촉 — 임베딩 외부 유지를 ADR에 분리 명시.
- **hard-invariant 완화는 사용자 소유**: `ADR-20260702-risk-designdecision-load-contract:92`가 provider-free를 hard invariant로 선언하고 완화를 별도 결정으로 유예했다. 이 완화는 **intent-level 충돌**(§4-10)이므로 사용자만 풀 수 있다 — **deep-interview에서 이미 승인됨**(전 스펙트럼 열림 확인 + 착수 지시, dialectic 수렴 #5). ADR에 "사용자가 intent를 해소, 에이전트가 방법을 결정"을 기록한다.
- **change_condition**: 이 결정의 철회 트리거는 supersede ADR에 명시한다. 후보: (i) 격리가 실제로 유지되지 않아 probe가 curate를 잡기 시작(E1 위협), (ii) VG2에서 git-persist rebuild-determinism이 거짓으로 판명(E3 위협). 둘 중 하나면 B 재검토.

---

## §9. 열린 검증 공백 (ac-9)

이월된 **검증 과제**이며, **설계 미결이 아니다**(§5의 in-scope 결정은 전부 닫힘).

- **VG1 — ADR 전수성**: 10개 중 5개 ADR만 정독했다. 나머지 5개(backfill · community-deterministic · communityreport · decision-lineage · branch-scoped)가 provider-free/생성 축을 접촉하는지 verify 노드가 전수 확인해야 한다. "영구 금지 ADR 부재"는 현재 전수 grep이 아닌 판단이다(§1.7).
- **VG2 — rebuild-determinism 실행 검증**: "생성 출력을 git-persist + MERGE 멱등하면 drop→reload가 실제로 동일 재현한다"는 설계 주장이다(§5.3 5단계, §6.1 E3). 실행으로 확인되지 않았다 — verify 노드가 drop→reload 재현 테스트로 B 행의 E3를 확정해야 한다.

두 항목 모두 백로그의 verification 과제로 넘긴다. 설계 확정을 막지 않는다.

---

## 부록 A. 인용 지도 (핵심)

- VISION: `docs/VISION.md:20-22,25-27,33,36,46-47,50-51,55-57`
- ADR: `ADR-20260626-foundational-architecture:19,27,34` · `ADR-20260701-semantic-layer-load-contract:6,17,19,23,26-28,33-34` · `ADR-20260701-v1-ontology-recall-reframe:11,19` · `ADR-20260702-risk-designdecision-load-contract:23,77-81,92` · `ADR-20260702-decision-lineage-freshness:9,47-48` · `ADR-20260704-semantic-embedding-load-contract:18,21,25,32,40`
- 코드/테스트(경로-스코프 probe·멱등 로더): `tests/recall/test_recall.py:117-145` · `tests/recall/test_recall_semantic.py:207-233` · `tests/kg/test_summary.py:384-409` · `tests/kg/test_risk.py:160-199` · `src/palimpsest/kg/summary.py:308`
- CodeQL 축: `docs/design-notes.md:194,200-211,220-239`
- 연구/provenance: `docs/research/precompute-hugrag-kg.md` · `docs/spikes/db-substrate-spike.md` · `.ditto/knowledge/DESIGN.md`
