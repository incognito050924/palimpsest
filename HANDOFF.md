# HANDOFF — palimpsest (cross-session)

다른 세션/PC 이어받기용. `.ditto/local/`은 gitignore라 안 넘어오므로 남은 작업은 **코드·계획 SoT 기준**으로 적는다. 이 문서는 배경 지침이지 권위가 아니다 — **계획 SoT는 `.ditto/knowledge/DESIGN.md`**, 사실·동작은 `src/palimpsest/`·ADR, 합의된 의도·수용기준은 work item. 새 세션에서 grep/test로 재확인할 것.

## 0. 사용자 목표 (북극성)
- **DESIGN.md 로드맵 전체를 끝까지 완주하는 것** (여러 세션 마라톤). 이번 세션은 그 일부.
- **불변식(잠긴 결정, ADR): provider-free** — palimpsest는 LLM을 절대 호출하지 않는다. inferred 층의 *생성·판정*은 전부 외부(ditto 등), palimpsest는 *적재·형식강제·회상*만. 이게 방향타다.

## 1. 전파 상태 (먼저 볼 것)
- **resume**: 브랜치 **`feat/community-node-wi2607010n6`** (main 아님). tip = **`3090973`**. main보다 **9 커밋 앞**, 0 뒤. 히스토리 재작성 없음. **push 안 함**(사용자: 나중에). **main 병합 미정 — 사용자 결정.**
- origin: `github.com/incognito050924/palimpsest.git`. **코퍼스 repo EcoleTreeSystems 읽기 전용 — git 작업 금지.**
- **인터프리터(이 PC)**: `~/.pyenv/shims/python`(3.13.5). `.venv` 없음(이전 핸드오프 PC와 다름). 설치: `~/.pyenv/shims/python -m pip install -e ".[test]"`.
- 테스트: `DITTO_AUTOPILOT_BYPASS=1 ~/.pyenv/shims/python -m pytest -q` (Docker 필요 — testcontainers Neo4j; `open -a Docker`). 현재 **88 passed**.

## 2. 이번 세션 landed (feat 브랜치, 96e341d 이후)
"외부 계약 초안 → 로더 실현" 아크로 **inferred 시맨틱 엔티티 온톨로지를 완성**(DESIGN §6 유예 #3 완전 실현). 각 슬라이스: TDD + fresh-context reviewer 독립검증(테스트 재현) + ADR/DESIGN/CLAUDE 실현 반영 + 커밋.
- `3f4ab66` **docs(knowledge)**: CommunityReport 적재 계약 ADR 초안(`ADR-20260702-communityreport-load-contract`).
- `54c0b22` **docs(knowledge)**: Risk·DesignDecision 적재 계약 ADR 초안(`ADR-20260702-risk-designdecision-load-contract` — ADR-20260701을 1급 inferred 엔티티로 일반화).
- `2b057f1` **feat(kg)**: CommunityReport 로더 — Community 대상 Summary에 **멤버십-grounding**(claim ref가 대상 community 멤버 Class로 resolve, 비멤버 entity-atomic 거부). `kg/summary.py` `_in_community`+`community:` 게이트.
- `6b158e4` **feat(kg)**: Risk 로더 — 새 `Risk` 노드 + `RISKS`(inferred), `kg/risk.py`. namespace id `risk:<sha256>`, ≥1 flag grounded, entity-atomic.
- `3090973` **feat(kg)**: DesignDecision 로더 — 새 `DesignDecision` 노드 + `DECIDES`/`SUPERSEDES`/`ADDRESSES_RISK`(inferred), `kg/decision.py`. namespace id `decision:<sha256>`, ≥1 DECIDES grounded, 엔티티-간 엣지 **라벨체크**(SUPERSEDES→DesignDecision·ADDRESSES_RISK→Risk), entity-atomic.

공통 규약(전 inferred 층): 전용 로더(generic deterministic ingest 재사용 금지 — 세탁 방지), `edge_kind='inferred'`, MERGE-on-id 멱등, namespace 격리 id, provenance+`code_bound_at`(대상 코드 committed_at), inferred 엣지는 recall `DEFAULT_RELATIONS`·`REL_TYPES`에서 제외(items 누출 없음). 테스트 47→88 passed.

## 3. 남은 작업 (완주까지) — DESIGN §6/§7 기준
**A. palimpsest 완주 가능(fixture로 hermetic 검증):**
- **설계위험 감지 (slice 2)** — DESIGN §6 다음 슬라이스, 원래 v1 후보. 이제 `Risk` 노드를 소비할 수 있다(구조적 결합 회상 위 위험 표시). **설계 무거움 → heavy path(`/ditto:deep-interview` → pre-mortem → autopilot) 권장.** 위험 *판정*은 외부(Risk 생산자), 구조적 감지·표시는 palimpsest.
- **신선도 2축** `valid_from`/`valid_to`(결정-계보, bi-temporal) · **벤치마크**(§7 성능 미측정) · **push/페르소나 회상** · **backfill**(전 git 이력) · **Reconcile**(브랜치 간 신선도) · **cross-repo** · **agent-trace 캡처**.
- **inferred 회상 전용 채널**(`recall_risk`/`recall_decision` 또는 'inferred' 채널) — 현재 Risk/DesignDecision는 순회 제외만 됨(전용 진입점 없음).

**B. 외부 생산자 필요(provider-free — 로더는 실현, 실데이터만 밖):** 요약/report/risk/decision **실생성**, 내용(semantic) 검증 판정 하네스.

**C. 사용자만 결정(내가 못 정함):** **노출 형태**(MCP/스킬/pluggable 택일) · **임베딩 설계**(부착대상/차원/하이브리드) · **provider-free 완화 여부**(현재 hard invariant). C는 닿는 슬라이스에서 확인.

## 4. 알려진 잔여 갭 (전부 low, 해당 ADR change_condition에 기록됨)
- **multi-flag/target `code_bound_at`**: Risk/DesignDecision가 여러 code 대상을 flag/decide할 때 노드+모든 엣지가 `sorted(...)[0]` 대상의 committed_at에 결박(각 엣지 자기 대상 아님). 단일 대상(테스트 커버)엔 정확. 회상 채널+multi 생산자 붙으면 엣지별 대상 결박으로 정련.
- **same-batch 엔티티 resolution**: DesignDecision의 SUPERSEDES/ADDRESSES_RISK 대상이 같은 배치에서 방금 적재된 엔티티면 미해소(현재 기존 그래프만). 두-pass 로더로 정련 가능.
- **DECIDES-only-decision `code_bound_at`=None**(코드 대상 없는 결정, untested edge) · **CommunityReport orphan**(멤버십 변경 시 옛 report가 recall stale로 안 잡힘) · **중첩 클래스 grounding**(CommunityReport, 계약 허용 범위).

## 5. 운영 교훈 (이 세션 검증 흐름 — 다음 세션 재사용)
- **슬라이스 = lightweight work item + TDD**로 몰았다. 큰 구현은 **implementer 서브에이전트에 위임**(코드+테스트만, ADR/DESIGN은 coordinator가), 이후 **fresh-context reviewer가 독립 검증**(코드 정독 + 테스트 재현 + 계약 대조). 서브에이전트 "성공" 보고는 증거 아님 — reviewer의 재현 테스트·diff가 증거.
- **reviewer ledger 정정**: reviewer가 `reviewer-output.json` + `ditto acg-review`로 verdict를 남긴다. finding 수정 후 같은 reviewer에게 SendMessage로 재확인 + ledger(findings=[]) 갱신 요청.
- **ac 증거 결박**: `ditto verify <wi> --criterion <ac> -- <cmd>` (테스트/grep). 4 AC 모두 pass여야 `ditto work done`(final_verdict=pass) 통과.
- **반복된 drift blind-spot**: ADR을 proposed→active로 승격할 때 **상단 관계(관계) bullet과 흩어진 DESIGN §2/§2-bis 마커를 자주 놓친다.** 실현 반영 후 반드시 `grep -n "유예\|proposed\|🔶\|계약 초안"`로 전수 감사할 것.
- `ditto knowledge adr-check`는 파일명/id 정합성만 검사(내용 아님).

## 6. 금지 (scope creep)
- EcoleTreeSystems git 작업 금지. 완료분(3 계약·3 로더) 재구현 금지.
- provider-free 위반 금지 — 판정/생성을 palimpsest 코드에 박지 말 것.
- code = SoT: `src/palimpsest/`가 권위. DESIGN §6 = 계획, ADR = 결정.

## 7. 새 세션 첫 확인
1. `git status` → 브랜치 `feat/community-node-wi2607010n6`, tip `3090973`, 9 ahead of main, clean.
2. `open -a Docker` 후 `~/.pyenv/shims/python -m pip install -e ".[test]"` → `DITTO_AUTOPILOT_BYPASS=1 ~/.pyenv/shims/python -m pytest -q` → **88 passed**.
3. 다음 작업 = **DESIGN §6 slice 2 '설계위험 감지'**(heavy path 권장) 또는 사용자가 지정하는 다른 완주-가능 슬라이스. C 결정이 필요한 슬라이스(노출/임베딩)는 사용자 확인 선결.
4. (선택) 오래된 draft work item `wi_260626v8v`(docs/DESIGN 위치이동으로 대체)·`wi_2606264gw`(DB 스파이크 ADR로 대체)는 superseded — `ditto work abandon` 정리 후보.
