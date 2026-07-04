"""고정된 커밋의 git provenance를 ``git``으로 한 번에 읽는다."""

from __future__ import annotations

import subprocess
from pathlib import Path

from palimpsest.ir import Provenance


def read_provenance(repo_path: Path | str, commit: str = "HEAD") -> Provenance:
    """``repo_path``의 ``commit``에 대해 source_commit / author / committed_at을 읽는다.

    ``git show`` 한 번만 써서 세 필드가 하나의 고정된 SHA에서 나오도록 한다.
    """

    repo_path = str(repo_path)

    def git(*args: str) -> str:
        out = subprocess.run(
            ["git", "-C", repo_path, *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()

    # %H = 전체 sha, %an/%ae = author 이름/이메일, %cI = committer 날짜 (ISO-8601 strict)
    line = git("show", "-s", "--format=%H%x1f%an <%ae>%x1f%cI", commit)
    sha, author, committed_at = line.split("\x1f")
    return Provenance(source_commit=sha, author=author, committed_at=committed_at)


def changed_paths(repo_path: Path | str, commit: str = "HEAD") -> list[str]:
    """``commit``이 바꾼 repo-상대 경로들을 ``git diff-tree``로 한 번에 얻는다.

    MODIFIES 엣지(Episode -> File)를 떠받친다: 각 커밋은 실제로 건드린 파일에만
    결박된다. 플래그는 각각 없어서는 안 될 것들이다:

    * ``--root`` — 부모 없는 ROOT 커밋을 빈 트리와 diff한다. 그래서 최초 import가
      아무것도가 아니라 트리 전체를 보고한다 (안 그러면 조용한 과소 포착).
    * ``--first-parent`` — 이 플래그 아래에서 머지 커밋은 *빈* diff-tree를 낸다
      (git이 머지에 대해 파일별 행을 내놓지 않음). 그래서 머지 Episode는 MODIFIES
      엣지에 **0** 기여한다. 이건 결함이 아니라 의도다: 머지가 합친 개별 변경들은
      이미 자기 커밋에 MODIFIES로 결박돼 있으므로, 머지 Episode에 다시 결박하면
      churn/co-change를 이중 계산하게 된다. 수용하는 공백: evil-merge(머지에만
      있고 두 부모 어디에도 없는 내용)는 어떤 Episode에도 결박되지 않는다 —
      churn/co-change가 가산 신호이기에 받아들이는 조용한 누락이다. 선형(비머지)
      이력에서는 이 플래그가 no-op이라 그대로 둔다.
    * ``--no-renames`` — 리네임을 rename 레코드가 아니라 delete-old + add-new
      (평범한 경로 둘)로 보고해, 모든 레코드가 단일 경로가 되도록 유지한다.
    * ``-z`` — NUL로 구분된 레코드 (공백/개행이 든 경로도 안전).
    * ``--no-commit-id`` — 앞머리의 commit-id 라인을 억제해 스트림이 순수한
      ``status\\0path\\0`` 쌍이 되게 한다.

    레코드는 ``status\\0path`` 쌍으로 온다. 토큰 수가 홀수면 스트림이 짝을 못 이뤘다는
    뜻(계약 위반)이므로, 경로를 잘못 정렬하느니 요란하게 실패한다(fail loud)
    (:func:`read_provenance`의 split-unpack 선례를 따른다). 삭제된 경로도 다른
    변경 경로와 똑같이 반환된다 — MODIFIES writer가 File 노드를 MATCH할 뿐
    (MERGE는 절대 안 함), 그래서 HEAD에 File 노드가 없는 경로는 그냥 엣지가
    안 생길 뿐 유령 File이 생기지 않는다.
    """

    out = subprocess.run(
        ["git", "-C", str(repo_path), "diff-tree", "--root", "--no-commit-id",
         "--no-renames", "--first-parent", "-r", "-z", "--name-status", commit],
        check=True, capture_output=True, text=True,
    ).stdout
    tokens = [t for t in out.split("\x00") if t]
    if len(tokens) % 2 != 0:
        raise ValueError(
            f"git diff-tree emitted an unpaired status/path stream for {commit!r}: "
            f"{len(tokens)} tokens"
        )
    return [tokens[i + 1] for i in range(0, len(tokens), 2)]
