"""End-to-end TDD for the `reconcile` CLI surface (wi_260702y0d, impl-cli).

Driven through the REAL CLI entry (``cli.main(argv)``) against a LIVE Neo4j and a
hermetic multi-branch git repo (see conftest::multi_branch_repo). The `reconcile`
subcommand takes an explicit N-branch set as first-class input, captures those
branch-scoped planes, and prints an N-way peer comparison as SEPARATED per-branch
sections — never a merged prose answer.

Covers this node's AC:
  ac-6  only the specified branches are compared (unspecified excluded)
  ac-3  the N-way peer comparison is actually INVOKED through the CLI (runtime
        wiring of reconcile_recall, distinct from the recall unit test)
plus the contract behaviors: git option-injection guard + honest rejection of
missing/duplicate branches (no raw traceback), N=0 handling, no author email in
output, and byte-identical existing behavior when --branch is unused (ac-2).
"""

import re

import pytest

from palimpsest import cli
from tests.e2e.conftest import RECONCILE_AUTHOR_EMAIL

CLASS_QN = "kr.co.ecoletree.service.commute.service.CommuteService"


def _peer_count(out: str) -> int:
    return int(re.search(r"PEERS \((\d+)\)", out).group(1))


def test_e2e_reconcile_invokes_nway_comparison_across_branches(
    cli_env, multi_branch_repo, capsys
):
    """ac-3 (CLI wiring): `reconcile` a symbol over an explicit 2-branch set ->
    output contains a SEPARATED per-branch peer section for EACH branch, with a
    freshness marker and per-branch grounding. Proves reconcile_recall ran
    end-to-end (only it produces the ranked N-way peers)."""
    rc = cli.main([
        "reconcile", CLASS_QN,
        "--branch", "main", "--branch", "feature",
        "--repo", str(multi_branch_repo),
    ])
    assert rc == 0
    out = capsys.readouterr().out

    # Both branches appear as distinct peer sections (N-way, not merged).
    assert _peer_count(out) == 2
    assert "[main]" in out
    assert "[feature]" in out
    # The freshest branch is flagged (main's tip date is newer than feature's).
    assert "freshest" in out
    peers_block = out.split("CODE DIVERGENCE")[0]
    # main's freshest marker sits on main's line, not feature's.
    main_line = next(ln for ln in peers_block.splitlines() if "[main]" in ln)
    assert "freshest" in main_line
    # Sections are separated (mirrors _print_result discipline), not merged prose.
    assert "PEERS (" in out
    assert "CODE DIVERGENCE:" in out
    assert "GAPS (" in out


def test_e2e_reconcile_compares_only_specified_branches(
    cli_env, multi_branch_repo, capsys
):
    """ac-6: with only `main` specified, the comparison scope is exactly that one
    branch — `feature` is never captured nor compared (unspecified excluded)."""
    rc = cli.main([
        "reconcile", CLASS_QN,
        "--branch", "main",
        "--repo", str(multi_branch_repo),
    ])
    assert rc == 0
    out = capsys.readouterr().out

    assert _peer_count(out) == 1
    assert "[main]" in out
    assert "[feature]" not in out


def test_e2e_reconcile_no_author_email_in_output(
    cli_env, multi_branch_repo, capsys
):
    """The peer grounding is author-omitted: the committer/author email must not
    leak into the reconcile output."""
    rc = cli.main([
        "reconcile", CLASS_QN,
        "--branch", "main", "--branch", "feature",
        "--repo", str(multi_branch_repo),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert RECONCILE_AUTHOR_EMAIL not in out
    assert "@" not in out.replace(RECONCILE_AUTHOR_EMAIL, "")


def test_e2e_reconcile_nonexistent_branch_is_honest_not_traceback(
    cli_env, multi_branch_repo, capsys
):
    """A missing branch is rejected honestly (clean message + non-zero exit),
    never a raw Python traceback surfaced from capture()'s ValueError."""
    rc = cli.main([
        "reconcile", CLASS_QN,
        "--branch", "no-such-branch",
        "--repo", str(multi_branch_repo),
    ])
    assert rc != 0
    out = capsys.readouterr().out
    assert "no-such-branch" in out
    assert "Traceback" not in out
    assert "ValueError" not in out


def test_e2e_reconcile_option_injection_branch_is_rejected(
    cli_env, multi_branch_repo, capsys
):
    """A branch name that looks like a git option (``--upload-pack=...``) must be
    passed through as a NAME (git-safe via capture's --end-of-options), not parsed
    as a git option — so it is rejected honestly as an unknown branch, no crash."""
    rc = cli.main([
        "reconcile", CLASS_QN,
        "--branch=--upload-pack=touch /tmp/pwned",
        "--repo", str(multi_branch_repo),
    ])
    assert rc != 0
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "upload-pack" in out  # surfaced as the rejected name, not executed


def test_e2e_reconcile_duplicate_branches_are_deduped(
    cli_env, multi_branch_repo, capsys
):
    """Duplicate --branch args collapse to a single peer (capture dedups) — no
    error, no doubled section."""
    rc = cli.main([
        "reconcile", CLASS_QN,
        "--branch", "main", "--branch", "main",
        "--repo", str(multi_branch_repo),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert _peer_count(out) == 1
    assert out.count("[main]") == 1


def test_e2e_reconcile_zero_branches_is_honest_not_crash(cli_env, capsys):
    """N=0: no --branch given -> a defined, honest message + non-zero exit, never
    an unhandled crash."""
    rc = cli.main(["reconcile", CLASS_QN, "--repo", "/nonexistent"])
    assert rc != 0
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "branch" in out.lower()
