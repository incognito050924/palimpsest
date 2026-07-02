# ADR-20260702-backfill-history-capture — 전 git 이력 backfill: git-archive replay + projection-over-history + Repo id 안정성

- 식별자: `ADR-20260702-backfill-history-capture` (파일명 = 불변 식별자)
- 상태: active (실현·검증: wi_260702asn — `backfill(driver, repo_path)` + CLI, 118 passed, fresh-context 독립 리뷰 PASS)
- 날짜: 2026-07-02
- work item: wi_260702asn (backfill 전 git 이력 캡처)
- 관계:
  - `ADR-20260626-foundational-architecture`의 **git=SoT·자동 캡처·전 이력 보존**(#2)을 이력 전체로 실현한다 — 기존 단일-커밋 ingest를 모든 커밋으로 확장.
  - 기존 결정론 파이프라인(`extract`→`augment_communities`→`ingest`)을 **그대로 재사용**한다(새 추출·적재 로직 없음). `ADR-20260701`의 provider-free 불변식 유지(생성형 0).
  - `DESIGN.md` §4 Capture/Preserve·§6 "전체 backfill" 슬라이스를 실현.

## 맥락

현재 `ingest`는 작업트리 1개 + 커밋 1개(HEAD)만 캡처한다(`cli.py _cmd_ingest`). 그러나 palimpsest의 정체성은 **전 이력 보존**(채택뿐 아니라 버려진 대안·이전 결정까지, 회귀 방지)이다. 전 커밋의 provenance(Episode)를 그래프에 넣어야 이력 위 회상·계보가 성립한다. git이 SoT이므로 KG는 그 이력을 재구축 가능한 projection으로 담으면 된다.

## 결정

1. **전 이력 replay.** `git log --format=%H --reverse`로 커밋을 oldest→newest 나열하고, 각 커밋마다: 그 커밋 트리를 **`git archive`로 임시 디렉터리에 materialize**(원 repo 비-mutating — `git checkout` 아님, working tree·HEAD 불변) → `read_provenance(repo, sha)` → `extract` → `augment_communities` → `ingest`. 즉 기존 단일-커밋 파이프라인을 이력 전체에 loop.

2. **projection 모델을 이력 전체에 적용.** 코드 노드는 MERGE-on-id라 oldest→newest 순서상 **newest가 `committed_at`을 이긴다**(HEAD 상태 반영). 각 커밋은 Episode 노드로 보존된다(전 이력). **커밋별 버전드 스냅샷 노드는 만들지 않는다** — 그건 별개의 더 큰 결정(범위 밖). "코드 노드=현재 projection + Episode=이력 스파인"이 모델.

3. **Repo id 안정성 불변식.** 각 커밋이 *서로 다른* 임시 디렉터리에 materialize되므로, `extract`의 기본 `repo_name=root.name`을 쓰면 커밋마다 다른 `Repo` 노드가 생긴다(`Repo.id == qualified_name == repo_name`). 따라서 `repo_name`을 **원 repo 이름으로 pin**해 단일 Repo 노드를 보장한다. 이 불변식은 테스트로 강제한다(`count(Repo)==1`, pin 제거 시 실패).

4. **provider-free·멱등.** git+extract+ingest만(LLM·네트워크 없음). 재실행은 MERGE-on-id라 Episode·노드 중복 없음.

## 실현·검증된 사항 (code = SoT — prose로 이중화하지 않음)

- `src/palimpsest/backfill.py`: `backfill(driver, repo_path)` — `git log --reverse` → 각 sha마다 `TemporaryDirectory` + `_materialize_tree`(`git archive --format=tar` → stdlib `tarfile.extractall(filter="data")`, path-traversal-safe) + `read_provenance` + `extract(tmp, prov, repo_name=<원 repo명>)` + `augment_communities` + `ingest`. `BackfillResult(commits, nodes, edges)`(nodes/edges=HEAD projection 크기, sum 아님).
- CLI: `backfill --repo PATH`(`cli.py`, 기존 ingest 미러).
- 테스트: `tests/backfill/test_backfill.py`(Episode==커밋수·ids==git log·newest-wins(newest≠oldest 가드)·멱등(Episode/Class/**Repo==1**)·repo 비-mutation status+HEAD) + `tests/backfill/conftest.py`(hermetic 2-커밋 git repo, 날짜 pin). 전체 118 passed, provider-free. fresh-context 독립 리뷰 PASS(behavior-risk finding 0).

## 근거 (rationale)

- **git archive, not checkout**: 대상 코퍼스는 읽기 전용이고 working tree를 건드리면 안 된다. `git archive`는 비-mutating으로 임의 커밋 트리를 얻는 표준 방법.
- **projection 모델 재사용, 새 시간모델 없음**: 코드 노드를 커밋별로 버전드하면 온톨로지·회상이 크게 달라진다(별개 결정). 최소·검증 원칙상 "노드=현재 + Episode=이력"으로 두고, 필요해지면 버전드를 별도 결정한다.
- **Repo pin이 correctness**: temp-dir 이름이 새면 Repo가 커밋 수만큼 증식 → 멱등·단일 그래프 위반. pin은 embellishment가 아니라 정합성 수정.

## 유예·범위 밖 (명시)

- **커밋별 버전드 스냅샷 노드** — 코드 노드를 커밋별로 보존(현재는 HEAD projection만). 온톨로지 확장 결정, 별개.
- **per-commit 변경 diff 링크** — 각 커밋이 *어떤* 파일/심볼을 바꿨는지(`MODIFIES` per commit)의 정밀 링크. 현재는 트리 전체 재적재(변경분 특정 없음).
- **changed-files-only 최적화** — 긴 이력에서 커밋마다 전 트리 재추출은 비싸다. 변경 파일만 추출하는 최적화는 후속.
- **빈 repo / 0-커밋 경로** — `commits=0` 반환(graceful), 미검증(테스트 없음).

## 철회·변경 조건 (change_condition)

- **버전드 노드 필요 시**: 이력 위 코드 *상태* 질의("커밋 C에서 이 메서드는?")가 필요해지면 커밋별 버전드 노드를 별도 ADR로 결정한다.
- **성능**: 긴 이력에서 전-트리 재추출이 병목이면 changed-files-only + `MODIFIES` per-commit로 정련한다.
- **0-커밋 경로**: 실사용에서 빈/비-repo 경로 처리가 필요하면 명시적 에러 vs graceful no-op를 결정하고 테스트를 추가한다.
