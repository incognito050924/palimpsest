"""End-to-end TDD for the palimpsest CLI (n5-impl-cli-e2e).

The whole deterministic slice — extract -> ingest -> recall — is driven through
the REAL CLI entry (``cli.main(argv)``, the same function ``python -m palimpsest``
calls) against a LIVE Neo4j (see conftest). One behavior at a time.

Proves ac-2 (a query returns grounded, bounded related code through the CLI) and
ac-3 (a query that resolves nothing states an explicit gap, never a confident
empty answer, with sections kept separate).
"""

import json
import os
import re
import subprocess
import sys
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


# --- ac-3: `load` an externally-produced summary payload through the CLI -------


def _summary_dict(target_id, refs, generator="ext-generator"):
    """A JSON-wire summary object (mirrors ir.Summary.to_dict)."""
    return {
        "target_id": target_id,
        "claims": [{"text": "grounded claim", "source_refs": list(refs)}],
        "generator": generator,
        "model": "ext-model-v1",
        "source_commit": "deadbeef",
        "created_at": "2026-07-01T09:00:00+09:00",
        "prompt": "summarize",
        "confidence": 0.9,
    }


def test_e2e_load_valid_payload_loads_and_unresolved_is_rejected(
    cli_env, capsys, tmp_path
):
    """ac-3: after `ingest`, the `load` subcommand reads an external summary JSON
    payload through the REAL CLI entry. A payload whose target + refs resolve LOADS;
    a payload with an unresolved ref is REJECTED (summary-atomic) and the rejection
    is surfaced in the output, never silently dropped."""
    assert cli.main(["ingest", "--repo", str(FIXTURES)]) == 0
    capsys.readouterr()  # drop ingest output

    # A valid payload: target + claim ref are real ingested nodes.
    good = tmp_path / "good.json"
    good.write_text(json.dumps([_summary_dict(CTRL_METHOD, [CTRL_METHOD, SVC_METHOD])]))
    assert cli.main(["load", str(good)]) == 0
    out = capsys.readouterr().out
    assert "LOADED 1/1" in out          # loaded count surfaced
    assert "REJECTED (0)" in out

    # An unresolved-ref payload: rejected summary-atomic, reason surfaced.
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([_summary_dict(CTRL_METHOD, ["com.does.not.Exist#nope()"])]))
    assert cli.main(["load", str(bad)]) == 0
    out = capsys.readouterr().out
    assert "LOADED 0/1" in out          # nothing loaded
    assert "REJECTED (1)" in out
    assert "unresolved" in out          # the honest rejection reason, not silence


# --- ac-4: the CLI load path imports NO generative module (provider-free) ------


FORBIDDEN_GENERATIVE_MODULES = [
    "openai", "anthropic", "langchain", "langchain_core", "llama_index",
    "transformers", "cohere", "neo4j_graphrag", "vertexai",
    "google.generativeai", "litellm", "sentence_transformers",
]


def test_cli_load_path_imports_no_generative_module():
    """A fresh interpreter importing the CLI load path (cli + the summary loader
    it wires) must load none of the LLM/generative libraries — palimpsest calls no
    model; the summary payload is produced externally."""
    src = Path(__file__).parents[2] / "src"
    probe = (
        "import importlib, sys, json\n"
        "cli = importlib.import_module('palimpsest.cli')\n"
        "assert hasattr(cli, '_cmd_load'), 'load subcommand missing'\n"
        f"forbidden = {FORBIDDEN_GENERATIVE_MODULES!r}\n"
        "bad = sorted({m for m in sys.modules for p in forbidden "
        "if m == p or m.startswith(p + '.')})\n"
        "print(json.dumps(bad))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True, text=True, check=True,
    )
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert loaded == [], f"CLI load path imported generative modules: {loaded}"

