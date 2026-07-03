# ADR-20260703-branch-scoped-node-identity — 브랜치 스코프 노드 정체성: id의 branch 차원 (versioned-by-branch)

- 식별자: `ADR-20260703-branch-scoped-node-identity` (파일명 = 불변 식별자)
- 상태: active (실현·검증: wi_260702y0d — Reconcile 브랜치 스코프 캡처, 전체 155 passed·provider-free)
- 날짜: 2026-07-03
- work item: wi_260702y0d (Reconcile: branch-scoped context)
- 관계:
  - `ADR-20260702-backfill-history-capture` §결정2("코드 노드=현재 projection, 커밋별 버전드 노드는 만들지 않는다")를 **branch 축에 한해 supersede**한다. 그 ADR의 철회조건("버전드 노드 필요 시 별도 ADR")이 발동한 별도 기록이다. 커밋별 버전드는 여전히 유예 — 여기서 versioned되는 축은 **branch뿐**이다.
  - `ADR-20260701-v1-ontology-recall-reframe`가 slice 2로 유예한 **design-risk slice**(브랜치 간 설계위험 감지)의 온톨로지 토대를 실현한다. 같은 ADR의 **provider-free 불변식**을 유지한다(reconcile 회상은 기존 외부 판정만 표시, 생성 0, LLM 0).
  - `ADR-20260626-foundational-architecture`와 정합 — 모든 엔티티 1급, git=SoT라 재구축 가능한 projection.

## 맥락

backfill(ADR-20260702-backfill-history-capture)은 **한 줄**의 이력을 bare-id(qualified_name) 평면에 replay한다. 코드 노드는 MERGE-on-id·newest-wins projection이라, 두 브랜치가 **같은 심볼**(동일 qualified_name)을 서로 다르게 갖고 있을 때 하나의 노드로 붕괴한다 — 캡처 순서에 따라 한쪽 `committed_at`이 다른 쪽을 덮어쓰고, 두 버전이 공존·비교될 수 없다. Reconcile(브랜치 간 설계위험 감지)의 전제는 "같은 심볼의 divergent 버전이 별개의 비교 가능한 노드로 공존"이다. 따라서 노드 정체성에 **브랜치 차원**이 필요하다 — backfill이 유예했던 versioned 노드가, branch 축에서 필요해졌다.

## 결정

1. **branch를 id의 차원으로 접는다 (정체성, 엣지/속성 전용 아님).** `Node.branch: Optional[str]=None`. `Node.id`는 branch가 None이면 bare `qualified_name`, named branch면 `branch:{branch}\x1f{qualified_name}`(Unit Separator 0x1f, 네임스페이스 프리픽스 — `summary:`/`community:`처럼 bare qualified_name과 절대 충돌 불가). 이 id가 곧 MERGE 키이자 라벨별 uniqueness CONSTRAINT의 대상이므로, branch는 **정체성**에 접힌다(멤버십을 엣지·속성으로만 표현하지 않는다). code=SoT: `src/palimpsest/ir.py`(`branch_scoped_id`, `Node.id`, `scope_to_branch`).

2. **순수 post-extract 변환으로 적용한다.** `scope_to_branch(ir, branch)`는 입력 IR을 변형하지 않고 새 IR을 반환한다 — 모든 코드 노드에 branch를 stamp하고 모든 엣지 src/dst를 **같은 순수 함수** `branch_scoped_id`로 다시 쓴다(노드 id ↔ 엣지 endpoint 일관성, 캡처 순서 불변). 한 번 추출한 base IR을 여러 브랜치로 fan-out할 수 있다. `capture`(`src/palimpsest/reconcile.py`)는 커밋 UNION을 한 번 열거하고 각 유니크 트리를 **한 번** 추출한 뒤, 그 커밋을 포함하는 브랜치마다 scope+ingest한다(EXTRACT 축 dedup, HISTORY 축은 보존).

3. **branch를 노드 속성으로도 저장한다 (GC 판별자).** id에 접힌 branch는 `n.branch` 속성으로도 persist된다(`src/palimpsest/kg/ingest.py`). 이 속성이 lifecycle-GC의 키다: (2a) **scoped-rebuild** = `wipe_branch_plane`이 named 평면 전체를 캡처 시작 시 1회 DETACH DELETE 후 재-project(delete-then-project) → shrink/rebase/tip-move가 stale을 남기지 않음; (2b) **reaper** = `reap_dead_branches`가 `branch IS NOT NULL AND NOT branch IN $live`로 git에 없는 named 평면을 제거. 두 GC 모두 `branch IS NOT NULL` 가드로 **bare-id(unspecified) 평면은 절대 건드리지 않는다**.

4. **부분 캡처 정직성 + fail-closed.** 브랜치별 `CaptureManifest`는 그 브랜치의 모든 커밋이 ingest된 **후에만** `captured`로 뒤집힌다(중간 실패 시 `pending`). shallow repo는 어떤 추출보다 먼저 거부한다(잘린 그래프 미영속). branch 인자는 git-safe 검증(`--end-of-options`).

5. **회상은 생성 없이 비교만.** `reconcile_recall(driver, symbol, branches)`(`src/palimpsest/recall/graphrag.py`)은 caller의 브랜치 집합에서 `qualified_name==symbol`인 branch-scoped peer들을 **동등하게**(특권 브랜치 없음, branch명은 tiebreak-안정성 전용) `committed_at` 절대 시각으로 랭크하고 max 시각을 `freshest`로 표시한다. cross-branch 충돌은 **비생성** 2트랙으로 노출: `conflict_edges`(기존 CONFLICTS_WITH 엣지만, 신규 생성 없음) + `code_divergence`(peer들이 심볼은 같으나 source_commit이 다름 — 순수 계산 관찰, 엣지 미기록). semantic annotation은 이미 저장된 inferred 층에서 **읽어서 표시만**(LLM/provider 호출 0).

## 만족하는 제약 (a)-(e) (검증됨)

- **(a) 공유 심볼의 branch 구분**: 같은 qualified_name이 브랜치별 별개 노드로 공존(id에 branch 접힘 → 붕괴 없음).
- **(b) committed_at clobber 없음**: 브랜치 간 MERGE 타깃이 distinct → newest-wins가 브랜치를 넘어 덮어쓰지 않음.
- **(c) inferred 층 bare-id 결박 무이전(no-migration)**: 결박은 id-문자열 해소다. branch-미지정 payload는 동일하게 해소되고, branch-scoped payload는 distinct inferred id를 파생 → risk/summary/decision/CONFLICTS_WITH 결박이 마이그레이션 없이 보존.
- **(d) 캡처 순서 불변**: id가 순수 함수 → 어떤 순서로 캡처해도 같은 그래프.
- **(e) branch-미지정 ⇒ byte-identical/additive**: `branch=None`이 bare qualified_name을 그대로 반환 → 기존 단일-브랜치 캡처와 바이트 동일, 순수 추가(검증: 전체 155 passed, ac-2).

## 기각된 대안

- **엣지 전용 멤버십**(bare-id MERGE + 무조건 committed_at SET): 공유 노드를 캡처 순서로 붕괴시켜 (a)공존·(d)결정성 위반. → 채택 안 함, 정체성에 접음.
- **항상 프리픽스 id / legacy를 "main" 브랜치로 접기**: byte-identical 추가성을 깨고 특권 브랜치를 만든다 → (e) 및 ac-3(no-privilege) 위반. → legacy bare-id 평면을 **unspecified 평면**으로 그대로 둠(마이그레이션 없음). stale named 평면은 scoped-rebuild + reaper로 GC(`branch IS NOT NULL`이 bare를 spare). Episode는 branch-agnostic 이력 스파인으로 유지(ac-5).

## 근거 (rationale)

- **정체성 vs 속성**: divergent 버전 공존은 MERGE 키가 브랜치를 구분해야만 성립한다. branch를 속성·엣지로만 두면 MERGE가 여전히 붕괴시킨다 — 그래서 id 차원으로 접는 것이 correctness이지 embellishment가 아니다.
- **순수 함수 하나로 노드·엣지 공유**: id와 endpoint를 같은 `branch_scoped_id`로 계산해야 캡처 순서·fan-out에도 endpoint가 노드와 어긋나지 않는다.
- **bare 평면 불가침**: 기존 이력(backfill)을 마이그레이션 없이 살리려면 unspecified 평면이 additive여야 하고, GC는 named 평면만 대상으로 해야 한다.
- **provider-free 유지**: reconcile은 기존 외부 판정을 surface만 한다. 위험 판정을 palimpsest가 생성하면 ADR-20260701 provider-free 불변식을 깬다.

## 유예·범위 밖 (명시)

- **커밋별 버전드 노드**(branch 축 밖): 한 브랜치 안 이력 위 코드 *상태* 질의는 여전히 Episode 스파인 + HEAD projection. branch만 versioned.
- **위험 판정 생성**: 브랜치 간 divergence를 "위험"으로 판정하는 라벨은 외부 생성물로만 적재(여기서 생성 안 함).

## 철회·변경 조건 (change_condition)

- **branch 너머 per-commit 버전드가 필요해지면**: 브랜치 ref를 branch-scoping 이상으로(커밋별) 버전드해야 하는 질의가 나오면 별도 ADR로 결정한다.
- **provider-free 완화 시**: reconcile이 판정을 *생성*해야 한다면(외부 표시가 아니라) 이 ADR과 ADR-20260701 provider-free 불변식을 함께 재론한다.
- **unspecified 평면 처리**: bare 평면을 named 평면과 통합해야 하는 요구가 실사용에서 나오면 마이그레이션 전략을 별도로 결정한다.
