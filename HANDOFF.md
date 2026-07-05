# HANDOFF — palimpsest 생성형 큐레이터 방향 재검토 (구현 인계)

> cross-PC handoff. `.ditto/local/`(work-item 레코드·intent.json·autopilot 그래프·dialectic 로그)은 **gitignore라 이 문서와 함께 오지 않는다.** 아래는 전부 **git-tracked 코드/문서** 기준이며, 새 PC에서 grep/read로 재확인할 것(이 문서는 권위 아님, §4-11).

## 0. 전파 상태 (먼저 읽기)
- 브랜치: **main**. resume: `git fetch origin && git checkout main && git pull --ff-only`. **history rewrite 없음**(fast-forward).
- 이 세션이 push하는 커밋 = 아래 "Landed" 2건 + 이 HANDOFF 커밋.
- **함께 오는 것(권위 산출물, git-tracked)**:
  - `docs/design/palimpsest-generative-curator-reexamination.md` — 잠근 whole-design
  - `.ditto/knowledge/adr/ADR-20260706-generative-curator-direction.md` — 결정 ADR(+change_condition)
  - `docs/design/palimpsest-reexamination-backlog.md` — 실행 백로그 T1–T10
- **함께 오지 않는 것**: `.ditto/local/work-items/wi_260705lxy`·`wi_260705nmm`(둘 다 done), 그 intent/autopilot/completion, scratchpad의 drift-map·dialectic-log(작업중 산출, 결론은 위 문서에 흡수됨). 새 PC에선 이 WI들이 없다 — "WI 닫기"는 무의미, 구현은 **새 WI**로 시작.

## 1. Landed this session (pushed)
- `b48bb1f` — whole-design 재검토 + ADR-20260706 + 백로그 (wi_260705lxy, done)
- `1a126c1` — CodeQL 축 정정: 주 tree-sitter 소유 정밀 + CodeQL 보조 (wi_260705nmm, done)

## 2. 결정 요지 (구현이 전제할 것 — 근거는 위 3문서)
- **방향 = 생성형 큐레이터 회복(Candidate B).** 격리 opt-in in-process 생산자(신설 `palimpsest.curate` + 별도 CLI)가 payload를 **git-SoT에 먼저 물질화** → **기존 멱등 inferred 로더**(`kg/summary.py:load_summaries` 등)로 적재. **content-verdict는 외부 유지**(자기인증 회피).
- **provider-free 정련(narrowing)**: 옛 "어디서도 LLM 0"에서 → **"recall+load 경로 LLM-free(경로-스코프 probe 유지) + 격리 opt-in 생산자 허용"**. ADR-20260701 §결정1을 부분 supersede(정식 edit은 백로그 T8, 구현 착지 후). ⚠ 옛 HANDOFF/문서의 "provider-free=절대 불변식" 서술은 ADR-20260706으로 대체됨.
- **정밀 콜그래프(구조/test-impact) = 주 tree-sitter 소유**: `extract/*.py`의 name-기반 over-matching을 `tags.scm`+`locals`+receiver-type로 개선(build-less·다언어·이력 균일). **CodeQL은 선택적 보조**(HEAD-only overlay, 니치=interprocedural taint/보안 Risk, 지금 드롭 가능). *stack-graphs는 2025-09 아카이브 — 쓰지 말 것.*

## 3. 다음 작업 (코드 기준 — 새 PC에서 fresh 재확인)
구현은 **새 work item**으로(백로그가 슬라이스 제공). 착수 순서:
- **First slice T1–T4** (백로그 §First slice): 격리 `curate` 생산자 → git 물질화 → 기존 로더 배선 → 격리 증명(경로-스코프 probe가 curate 있어도 green) → 재구축 결정론.
  - 재확인: `grep -n FORBIDDEN_GENERATIVE tests/recall/test_recall.py`(probe 경로-스코프 유지 확인), `src/palimpsest/kg/summary.py`의 `load_summaries`(멱등 로더), `src/palimpsest/cli.py`의 `load` 서브커맨드(외부 payload→로더 이미 존재 → curate는 앞단만 additive).
- **T9-PRIMARY** (병렬 가능): `extract/java.py`의 `_calls_edges`/`_collect_call_names`(현재 name-only over-matching) 정밀화. 재확인: `sed -n '209,264p' src/palimpsest/extract/java.py`.
- **검증 공백 VG2**: 생성출력 git-persist가 Neo4j drop→reload 동일 재현을 보장하는지 **실행 테스트**로 확정(백로그 T6). 미검증 상태.

## 4. Gotchas
- 테스트/Docker: 이 repo는 `.venv`(python3.12) + Docker로 실행(pyenv 없음).
- CodeQL 세부(직접실행 vs ditto소비, edge_kind 표기 C-Q5)는 T10 진입 시 확정 — 지금 미결(설계 open).
- 사소 follow-up: `CLAUDE.md`의 DITTO Knowledge 결정목록에 ADR-20260706 한 줄 투영(자동 투영 대상, 미적용).
- 사용자 지침: foundational 작업은 **품질 우선**("빨리"=시간압박 아님, 한 번에 제대로). 홀리스틱 요청에 조각내 미루기/조용한 축소 금지.
