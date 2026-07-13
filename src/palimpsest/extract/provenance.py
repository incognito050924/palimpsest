"""Read git provenance for a pinned commit, once, via ``git``."""

from __future__ import annotations

import subprocess
from pathlib import Path

from palimpsest.ir import Provenance


def read_provenance(repo_path: Path | str, commit: str = "HEAD") -> Provenance:
    """Read source_commit / author / committed_at for ``commit`` in ``repo_path``.

    Uses one ``git show`` so the three fields come from a single pinned SHA.
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

    # %H = full sha, %an/%ae = author name/email, %cI = committer date (ISO-8601 strict)
    line = git("show", "-s", "--format=%H%x1f%an <%ae>%x1f%cI", commit)
    sha, author, committed_at = line.split("\x1f")
    return Provenance(source_commit=sha, author=author, committed_at=committed_at)


def changed_paths(repo_path: Path | str, commit: str = "HEAD") -> list[str]:
    """The repo-relative paths ``commit`` changed, once, via ``git diff-tree``.

    Underpins the MODIFIES edge (Episode -> File): each commit binds only to the
    files it actually touched. Flags, each load-bearing:

    * ``--root`` — a parent-less ROOT commit is diffed against the empty tree, so
      the initial import reports its whole tree instead of nothing (silent
      under-capture otherwise).
    * ``--first-parent`` — under this flag a merge commit emits an *empty*
      diff-tree (git yields no per-file rows for a merge), so a merge Episode
      contributes **zero** MODIFIES edges. This is intended, not a defect: the
      individual changes a merge combined are already bound to their own commits
      via MODIFIES, so re-binding them to the merge Episode would double-count
      churn/co-change. Accepted gap: an evil-merge (content unique to the merge,
      present in neither parent) binds to no Episode — a silent omission we accept
      because churn/co-change are additive signals. On linear (non-merge) history
      the flag is a no-op, so it stays.
    * ``--no-renames`` — a rename is reported as delete-old + add-new (two plain
      paths) instead of a rename record, keeping every record a single path.
    * ``-z`` — NUL-delimited records (paths with spaces/newlines are safe).
    * ``--no-commit-id`` — suppress the leading commit-id line so the stream is
      pure ``status\\0path\\0`` pairs.

    Records arrive as ``status\\0path`` pairs; an odd token count means the stream
    did not pair up (off-contract) and we fail loud rather than mis-align paths
    (mirrors the split-unpack precedent in :func:`read_provenance`). A deleted
    path is returned like any other changed path — the MODIFIES writer MATCHes
    (never MERGEs) the File node, so a path with no HEAD File node simply gets no
    edge, never a phantom File.
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


# ---------------------------------------------------------------------------
# Cross-tier CALLS_API link transform-provenance (wi_260713iah, part 2).
#
# A cross-tier link's route is either read DIRECTLY at the call site (a literal) or
# DERIVED through a resolution step (one-hop param->uri dataflow, a dev-proxy rewrite,
# or @Value config host-grounding). A derived route is a weaker signal than a literal
# direct-match: it can NEVER carry the literal 1.0 confidence tier, and a derived
# resolution that is not provably unique (>1 matching context/binding) is capped harder
# still (the foreign-runtime-fidelity finding). This is the provenance model + the
# confidence CAP the matcher (extract/calls_api.py) applies. Kept here (not in ir.py)
# so the base module stays free of link-scoring policy; imported by the matcher only.
# ---------------------------------------------------------------------------

# The route was read DIRECTLY as a literal at the call site — the strongest, 1.0-eligible
# signal (no transform cap).
LITERAL = "literal"
# The route was RECOVERED by one-hop param->uri dataflow (a helper-passed URL).
DATAFLOW = "dataflow"
# The route was rewritten by a dev-proxy prefix mapping (mechanism A).
PROXY = "proxy"
# The target host was resolved from @Value config grounding (mechanism B).
CONFIG = "config"

# The DERIVED transforms — a link built on any of these is not a plain literal
# direct-match, so it is capped below the literal 1.0 tier.
DERIVED = frozenset({DATAFLOW, PROXY, CONFIG})

# Confidence cap for a derived link whose resolution IS provably unique (single
# matching context) — below the literal 1.0, at the templated tier: a recovered /
# rewritten / grounded route is at best as trustworthy as a templated literal match.
DERIVED_CAP = 0.7
# Cap for a derived link whose resolution is NOT provably unique (>1 matching
# context/binding) — the weakest positive signal.
AMBIGUOUS_CAP = 0.4


def is_derived(transform: str) -> bool:
    """Whether ``transform`` names a DERIVED resolution (vs a literal direct-match)."""
    return transform in DERIVED


def transform_confidence_cap(transform: str, unique: bool) -> float:
    """The upper bound a link's confidence may take, given how its route was resolved.

    A literal direct-match has no transform cap (1.0 stays reachable). A derived link
    (dataflow / proxy / config) is capped to :data:`DERIVED_CAP`; if additionally its
    resolution is not provably unique (>1 matching context/binding), it is capped to
    :data:`AMBIGUOUS_CAP`."""
    if transform not in DERIVED:
        return 1.0
    return DERIVED_CAP if unique else AMBIGUOUS_CAP


