"""전체 git 이력을 KG에 backfill한다(provider-free, 결정론적).

단일 커밋 ingest(``cli._cmd_ingest``)는 pin된 커밋 하나를 projection한다. Backfill은
바로 그 extract -> ingest 파이프라인을 모든(EVERY) 커밋에 oldest -> newest로 재생해,
결정론적 projection이 전체 이력을 반영하게 한다: 모든 커밋이 ``Episode``로 안착하고,
각 코드 노드의 freshness(``committed_at``)는 그 노드를 건드린 가장 최신(NEWEST) 커밋에
결박된다(``ingest``가 무조건적 SET으로 id 기준 MERGE하므로, oldest->newest 순서에서
마지막 쓰기가 이긴다 — last-write-wins).

각 커밋의 트리는 ``git archive``(stdlib ``tarfile``로 파이핑)를 통해 새(FRESH) 임시
디렉터리에 실체화된다 — ``git checkout``은 절대 쓰지 않는다 — 따라서 ``repo_path``의
작업 트리와 HEAD는 손대지 않는다. 순수 git + extract + ingest: LLM 없고, git과 Neo4j
외의 네트워크도 없다. 재실행은 멱등(idempotent)이다(MERGE-on-id).
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from palimpsest.extract import changed_paths, extract, read_provenance
from palimpsest.ir import branch_scoped_id
from palimpsest.kg import (
    augment_communities,
    create_constraints,
    ingest,
    ingest_modifies,
)


@dataclass(frozen=True)
class BackfillResult:
    """backfill 실행이 projection한 것.

    ``commits``는 재생된 커밋 수(oldest -> newest)다. ``nodes`` / ``edges``는 가장
    최신(NEWEST) 커밋의 IR 크기 — HEAD projection — 이며, 커밋 간 합이 아니다(합을
    내면 MERGE가 id 기준으로 dedup하는 노드를 이중 계산하게 된다).
    """

    commits: int
    nodes: int = 0
    edges: int = 0
    # 전체 재생에 걸쳐 안착한 Episode -[:MODIFIES]-> File 엣지의 총합(HEAD IR 크기인
    # ``nodes``/``edges``와 달리 커밋 간 합이다): MODIFIES는 커밋별 사실이므로 모든
    # 커밋이 각자의 엣지를 기여한다.
    modifies: int = 0


def _commits_oldest_first(repo_path: str) -> list[str]:
    """전체 SHA 목록, oldest -> newest. 빈 repo / 커밋 없음 -> ``[]``."""
    out = subprocess.run(
        ["git", "-C", repo_path, "log", "--format=%H", "--reverse"],
        check=False, capture_output=True, text=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def _materialize_tree(repo_path: str, sha: str, dest: str) -> None:
    """작업 트리를 건드리지 않고(WITHOUT) ``sha``의 트리를 ``dest``로 추출한다.

    ``git archive``가 pin된 트리의 tar를 stdout으로 스트리밍하면 stdlib ``tarfile``이
    풀어낸다 — ``git checkout``이 없으므로 ``repo_path``의 HEAD/작업 트리는 손대지
    않는다(backfill은 소스 repo에 대해 읽기 전용이다).
    """
    archive = subprocess.run(
        ["git", "-C", repo_path, "archive", "--format=tar", sha],
        check=True, capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


def backfill(driver, repo_path: Path | str) -> BackfillResult:
    """extract -> ingest를 모든 커밋에 oldest -> newest로 재생한다.

    멱등(idempotent, MERGE-on-id)이며 provider-free(순수 git + extract + ingest)다.
    """
    repo_path = str(repo_path)
    shas = _commits_oldest_first(repo_path)
    if not shas:
        return BackfillResult(commits=0)

    # 커밋 간 안정적인 Repo id: 각 커밋은 서로 다른(DIFFERENT) 임시 디렉터리에
    # 실체화되므로, extract의 기본값 ``repo_name = root.name``은 커밋마다 달라져
    # 매번 새 Repo 노드를 찍어낸다. 이를 소스 repo의 이름으로 pin해 모든 커밋이
    # 같은(SAME) Repo로 projection되게 한다(그리고 실제 repo 경로를 넘기는 단일 커밋
    # ingest와 일치시킨다).
    repo_name = Path(repo_path).resolve().name

    create_constraints(driver)
    nodes = edges = modifies = 0
    for sha in shas:
        with tempfile.TemporaryDirectory() as tmp:
            _materialize_tree(repo_path, sha, tmp)
            prov = read_provenance(repo_path, sha)
            ir = extract(tmp, prov, repo_name=repo_name)
            augment_communities(ir, prov)
            ingest(driver, ir)
            # MODIFIES: 이 커밋의 Episode를 그 커밋이 변경한 File에만 결박한다.
            # File id는 backfill이 projection하는 bare(branch=None) plane을 공유하므로
            # ``branch_scoped_id(None, path) == path`` — File 노드가 쓰는 것과 같은(SAME)
            # 정체성 함수여서 엔드포인트가 일관된다(삭제된 경로는 어떤 File 노드에도
            # 해석되지 않아 라이터의 MATCH에서 건너뛰어지며, phantom이 되지 않는다).
            rows = [
                {"episode_id": sha, "file_id": branch_scoped_id(None, path),
                 "committed_at": prov.committed_at}
                for path in changed_paths(repo_path, sha)
            ]
            modifies += ingest_modifies(driver, rows)
            nodes, edges = len(ir.nodes), len(ir.edges)

    return BackfillResult(
        commits=len(shas), nodes=nodes, edges=edges, modifies=modifies
    )
