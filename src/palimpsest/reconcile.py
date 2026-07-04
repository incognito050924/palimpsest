"""N-way branch capture: 여러 branch의 전체 이력을 KG에 projection한다.

Backfill(:mod:`palimpsest.backfill`)은 extract -> ingest를 이력 한(ONE) 갈래에 대해
bare-id plane으로 재생한다. Reconcile은 이를 N개 branch로 일반화해, 같은(SAME) 심볼의
갈라진 버전들이 뭉개지지 않고 구별되는·비교 가능한 노드로 공존하게 한다(ac-1) — 각
branch는 MERGE 키에 접히는 자기만의 정체성 네임스페이스(``scope_to_branch``)를 가진다.

설계 불변식(provider-free — LLM 없고, git + Neo4j 외의 네트워크도 없다):

  * EXTRACT 축은 dedup하되, HISTORY 축은 절대 dedup하지 않는다. 커밋의 합집합(UNION)을
    한 번 열거하고(``git rev-list --reverse --date-order``) 각 고유 트리를 한 번(ONCE)
    실체화 + 추출한다; 그렇게 나온 IR을 그 커밋을 포함하는 branch마다 한 번씩 scope +
    ingest한다(membership fan-out). branch별 전체 이력이 보존된다(도달 가능한 모든
    커밋이 Episode로 안착) — merge-base 절단은 거부된다(ac-5).
  * scoped-rebuild(delete-then-project): 지정된 각 branch의 plane을 처음에 한 번(ONCE)
    비운다(``wipe_branch_plane``). 그래서 shrink/rebase/tip-move가 낡은 노드를 남기지
    않는다. bare-id plane은 절대 건드리지 않는다(ac-6).
  * 부분 capture 정직성: branch별 ``CaptureManifest``는 그 branch의 모든 커밋이 ingest된
    뒤에만(AFTER) ``captured``로 뒤집힌다 — 실행 도중 실패하면 ``pending``으로 남는다
    (조용히 captured되지 않는다).
  * fail-closed: shallow repository는 어떤 extract보다 먼저 거부된다(절단된 그래프는
    절대 영속되지 않는다). branch 인자는 git-safe하고(검증되며, git 옵션으로 절대
    파싱되지 않음) dedup된다.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from palimpsest.backfill import _materialize_tree
from palimpsest.extract import extract, read_provenance
from palimpsest.ir import scope_to_branch
from palimpsest.kg import augment_communities, ingest
from palimpsest.kg.ingest import (
    CAPTURE_MANIFEST,
    create_constraints,
    wipe_branch_plane,
)

# Unit Separator — CaptureManifest id에서 capture 키(정렬된 branch 이름들)와 branch를
# 잇는다; ir.py의 branch-scoped id 구분자와 동일하다.
_US = "\x1f"


@dataclass(frozen=True)
class CaptureResult:
    """N-way capture가 projection한 것.

    ``branches``는 실제로 capture된, dedup·정렬·검증된 branch 집합이다.
    ``commits``는 그 이력들의 합집합(UNION) 크기다(고유 커밋, 각각 한 번씩
    실체화 + 추출).
    """

    branches: tuple[str, ...]
    commits: int


def _git(repo_path: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_path, *args],
        check=False, capture_output=True, text=True,
    )


def _is_shallow(repo_path: str) -> bool:
    """repo가 shallow clone(절단된 이력)일 때에만 True."""
    out = _git(repo_path, "rev-parse", "--is-shallow-repository")
    if out.returncode == 0:
        val = out.stdout.strip().lower()
        if val in ("true", "false"):
            return val == "true"
    # 이 플래그가 없는 git 버전을 위한 fallback: shallow marker 파일.
    marker = _git(repo_path, "rev-parse", "--git-path", "shallow")
    if marker.returncode == 0:
        p = Path(marker.stdout.strip())
        if not p.is_absolute():
            p = Path(repo_path) / p
        return p.exists()
    return False


def _validate_branches(repo_path: str, branches) -> list[str]:
    """dedup + 정렬 + 검증. git-safe: 각 ref는 ``--end-of-options`` 뒤에서(AFTER)
    커밋으로 peel되므로 ``-x`` 같은 이름이 git 옵션으로 파싱될 수 없다.
    존재하지 않는 branch는 raw CalledProcessError가 아니라 정직하게 거부된다
    (ValueError)."""
    if not branches:
        raise ValueError("capture requires at least one branch")
    uniq = sorted(set(branches))
    for b in uniq:
        out = _git(
            repo_path, "rev-parse", "--verify", "--quiet",
            "--end-of-options", f"{b}^{{commit}}",
        )
        if out.returncode != 0:
            raise ValueError(f"unknown or invalid branch: {b!r}")
    return uniq


def _head_sha(repo_path: str, branch: str) -> str:
    """branch tip 커밋. git-safe한 ``--verify`` peel 형식을 쓴다(객체 이름만
    출력한다 — 맨(bare) ``--end-of-options``였다면 그대로 echo됐을 것이다)."""
    out = _git(
        repo_path, "rev-parse", "--verify", "--end-of-options",
        f"{branch}^{{commit}}",
    )
    out.check_returncode()
    return out.stdout.strip()


def _rev_list_union(repo_path: str, branches: list[str]) -> list[str]:
    """어느 branch에서든 도달 가능한 커밋의 합집합(UNION), 각각 한 번씩, oldest-first."""
    out = _git(
        repo_path, "rev-list", "--reverse", "--date-order",
        "--end-of-options", *branches,
    )
    out.check_returncode()
    return [x for x in out.stdout.splitlines() if x]


def _membership(repo_path: str, branches: list[str]) -> dict[str, set[str]]:
    """commit SHA -> 그것을 포함하는 branch 집합(branch별 rev-list를 뒤집는다)."""
    membership: dict[str, set[str]] = {}
    for b in branches:
        out = _git(repo_path, "rev-list", "--end-of-options", b)
        out.check_returncode()
        for sha in out.stdout.splitlines():
            if sha:
                membership.setdefault(sha, set()).add(b)
    return membership


_MANIFEST_MERGE = """
MERGE (m:CaptureManifest {id: $id})
SET m.capture_key = $capture_key,
    m.branch       = $branch,
    m.status       = $status,
    m.commit_count = $commit_count,
    m.head_sha     = $head_sha,
    m.captured_at  = $captured_at
"""


def _write_manifest(driver, capture_key, branch, *, status, commit_count,
                    head_sha, captured_at) -> None:
    mid = f"capture:{capture_key}{_US}{branch}"
    with driver.session() as session:
        session.run(
            _MANIFEST_MERGE, id=mid, capture_key=capture_key, branch=branch,
            status=status, commit_count=commit_count, head_sha=head_sha,
            captured_at=captured_at,
        )


def capture(driver, repo_path: Path | str, branches) -> CaptureResult:
    """각 branch의 전체 이력을 그 branch만의 정체성 plane으로 projection한다.

    멱등(idempotent, branch별 delete-then-project 후 MERGE-on-id)이며
    provider-free다. 어떤 extract보다 먼저 shallow repo를 거부한다(fail-closed).
    """
    repo_path = str(repo_path)
    if _is_shallow(repo_path):
        raise RuntimeError(
            f"refusing to capture a shallow repository at {repo_path!r}: "
            "full history is required (ac-5); re-clone without --depth"
        )
    branches = _validate_branches(repo_path, branches)
    repo_name = Path(repo_path).resolve().name

    create_constraints(driver)
    # scoped-rebuild: 지정된 각 plane을 처음에 한 번(ONCE) 비운다(커밋별로는 절대 아님).
    for b in branches:
        wipe_branch_plane(driver, b)

    membership = _membership(repo_path, branches)
    union = _rev_list_union(repo_path, branches)

    per_branch_commits = {b: 0 for b in branches}
    for brs in membership.values():
        for b in brs:
            per_branch_commits[b] += 1

    capture_key = _US.join(branches)
    for b in branches:
        head = _head_sha(repo_path, b)
        _write_manifest(
            driver, capture_key, b, status="pending",
            commit_count=per_branch_commits[b], head_sha=head, captured_at=None,
        )

    # EXTRACT 축을 dedup: 각 고유 커밋을 한 번(ONCE) 실체화 + 추출한 뒤, 같은(SAME)
    # base IR을 그것을 포함하는 각 branch로 펼친다(scope + ingest).
    for sha in union:
        with tempfile.TemporaryDirectory() as tmp:
            _materialize_tree(repo_path, sha, tmp)
            prov = read_provenance(repo_path, sha)
            base_ir = extract(tmp, prov, repo_name=repo_name)
            augment_communities(base_ir, prov)
            for b in sorted(membership[sha]):
                ingest(driver, scope_to_branch(base_ir, b))

    # 부분 capture 정직성: 모든(ALL) 커밋이 ingest된 뒤에만 `captured`로 뒤집는다.
    now = datetime.now(timezone.utc).isoformat()
    for b in branches:
        head = _head_sha(repo_path, b)
        _write_manifest(
            driver, capture_key, b, status="captured",
            commit_count=per_branch_commits[b], head_sha=head, captured_at=now,
        )

    return CaptureResult(branches=tuple(branches), commits=len(union))
