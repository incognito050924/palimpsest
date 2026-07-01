"""End-to-end TDD for the palimpsest CLI (n5-impl-cli-e2e).

The whole deterministic slice — extract -> ingest -> recall — is driven through
the REAL CLI entry (``cli.main(argv)``, the same function ``python -m palimpsest``
calls) against a LIVE Neo4j (see conftest). One behavior at a time.

Proves ac-2 (a query returns grounded, bounded related code through the CLI) and
ac-3 (a query that resolves nothing states an explicit gap, never a confident
empty answer, with sections kept separate).
"""

import re
from pathlib import Path

from palimpsest import cli
from palimpsest.extract import read_provenance

# The n2 `commute` fixture graph (see tests/extract/fixtures).
FIXTURES = Path(__file__).parents[1] / "extract" / "fixtures"

IFACE = "kr.co.ecoletree.service.commute.service.CommuteService"
CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
# A controller Method that CALLS the matching service Method (from the extractor).
CTRL_METHOD = CTRL + "#selectAttedanceCondition(Map,HttpServletRequest)"
SVC_METHOD = IFACE + "#selectAttedanceCondition(Map)"

TOTAL_NODES = 28  # whole fixture graph (Repo + 2 Package + 2 File + 2 Class + 21 Method)


def _item_count(out: str) -> int:
    return int(re.search(r"ITEMS \((\d+)\)", out).group(1))


def test_e2e_ingest_then_query_returns_grounded_bounded_related_code(cli_env, capsys):
    """ac-2: `ingest` the fixture tree, then `query` a known Method through the
    CLI -> output contains the related code it CALLS + its containing Class, each
    grounded with commit + file:line, and the result is bounded (not the whole
    graph; the limit is respected)."""
    prov = read_provenance(FIXTURES)  # real git provenance for the fixtures

    assert cli.main(["ingest", "--repo", str(FIXTURES)]) == 0
    assert "ingested" in capsys.readouterr().out

    # A known Method -> its CALLS callee and its CONTAINS parent Class, grounded.
    assert cli.main(["query", CTRL_METHOD, "--depth", "1", "--limit", "25"]) == 0
    out = capsys.readouterr().out

    assert SVC_METHOD in out            # the CALLS callee (related code)
    assert CTRL in out                  # the CONTAINS parent Class
    assert prov.source_commit in out    # real commit grounding
    assert ".java:" in out              # file:line grounding

    # Bounded by the depth: far fewer than the whole fixture graph.
    n_items = _item_count(out)
    assert 0 < n_items < TOTAL_NODES

    # The limit is respected: a broad seed capped to 3 yields <=3 items + a MORE
    # marker for the unexpanded frontier (never the whole graph loaded).
    assert cli.main(["query", CTRL, "--depth", "1", "--limit", "3"]) == 0
    capped = capsys.readouterr().out
    assert _item_count(capped) <= 3
    assert "MORE:" in capped


def test_e2e_query_nonexistent_symbol_states_explicit_gap(cli_env, capsys):
    """ac-3: a query that resolves nothing must state an explicit gap through the
    CLI — never a confident empty answer — with the gaps kept in their own
    section, separate from items."""
    assert cli.main(["ingest", "--repo", str(FIXTURES)]) == 0
    capsys.readouterr()  # drop ingest output

    assert cli.main(["query", "com.does.not.Exist#nope()", "--depth", "2"]) == 0
    out = capsys.readouterr().out

    # No related code, and that emptiness is stated as an explicit gap.
    assert _item_count(out) == 0
    assert "GAPS (" in out
    assert re.search(r"GAPS \((\d+)\)", out).group(1) != "0"
    assert "did not resolve" in out          # the honest gap text, not silence
    assert "com.does.not.Exist#nope()" in out

    # Sections stay separate: the gap is not smuggled into ITEMS.
    items_block = out.split("GAPS")[0]
    assert "did not resolve" not in items_block

