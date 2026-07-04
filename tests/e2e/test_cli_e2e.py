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


# --- ac-1/ac-2: durability — load a git-tracked payload DIRECTORY + rebuild -----


def _summary_snapshot(driver):
    """The inferred layer as (Summary ids, SUMMARIZES (summary_id, target_id) pairs)."""
    with driver.session() as session:
        nodes = sorted(r["id"] for r in session.run("MATCH (s:Summary) RETURN s.id AS id"))
        edges = sorted(
            (r["sid"], r["tid"])
            for r in session.run(
                "MATCH (s:Summary)-[:SUMMARIZES]->(t) RETURN s.id AS sid, t.id AS tid"
            )
        )
    return nodes, edges


def _write_payload_dir(root):
    """A git-tracked payload DIRECTORY: two JSON files, each an array of summaries,
    all grounded in real ingested fixture nodes."""
    root.mkdir()
    (root / "a.json").write_text(
        json.dumps([_summary_dict(CTRL_METHOD, [CTRL_METHOD, SVC_METHOD])])
    )
    (root / "b.json").write_text(json.dumps([_summary_dict(SVC_METHOD, [SVC_METHOD])]))
    return root


def test_e2e_load_directory_batch_loads_every_payload(cli_env, capsys, tmp_path):
    """ac-1: `load` accepts a DIRECTORY and batch-loads EVERY summary JSON payload
    file inside it (two files -> two summaries), printing the loaded/rejected
    counts. The single-file path (covered above) keeps working."""
    assert cli.main(["ingest", "--repo", str(FIXTURES)]) == 0
    capsys.readouterr()  # drop ingest output

    payload_dir = _write_payload_dir(tmp_path / "summaries")
    assert cli.main(["load", str(payload_dir)]) == 0
    out = capsys.readouterr().out
    assert "LOADED 2/2" in out          # both files' summaries loaded
    assert "REJECTED (0)" in out


def test_e2e_rebuild_from_git_payload_dir_restores_summaries(cli_env, tmp_path):
    """ac-2: durability proof. Load summaries from a git payload dir, DROP every
    Summary node + SUMMARIZES edge from Neo4j (a Neo4j rebuild), then reload from
    the SAME dir — the identical Summary nodes + edges are restored because the ids
    are deterministic (git = SoT, Neo4j = re-buildable projection)."""
    assert cli.main(["ingest", "--repo", str(FIXTURES)]) == 0

    payload_dir = _write_payload_dir(tmp_path / "summaries")
    assert cli.main(["load", str(payload_dir)]) == 0

    driver = cli_env.get_driver()
    try:
        before = _summary_snapshot(driver)
        assert before[0], "expected Summary nodes after the initial load"
        assert before[1], "expected SUMMARIZES edges after the initial load"

        # Neo4j drop: wipe the inferred layer (the projection), keep git SoT.
        with driver.session() as session:
            session.run("MATCH (s:Summary) DETACH DELETE s")
        assert _summary_snapshot(driver) == ([], [])

        # Rebuild from the git payload dir.
        assert cli.main(["load", str(payload_dir)]) == 0
        after = _summary_snapshot(driver)
    finally:
        driver.close()

    assert after == before  # same deterministic ids, same edges restored


# --- semantic verdict rides the git payload + survives a Neo4j drop→reload ------


def test_e2e_semantic_verdict_survives_drop_and_reload(cli_env, tmp_path):
    """The externally-produced semantic verdict rides on the git-tracked payload,
    so a DETACH DELETE of the inferred layer + reload from the same dir restores it
    (git = SoT, Neo4j = rebuildable projection). Verdict is not in the summary id."""
    assert cli.main(["ingest", "--repo", str(FIXTURES)]) == 0

    verdict = {"verdict": "unfaithful", "judge": "ditto", "model": "judge-model-v1"}
    payload = _summary_dict(CTRL_METHOD, [CTRL_METHOD, SVC_METHOD])
    payload["semantic_verdict"] = verdict
    payload_dir = tmp_path / "summaries"
    payload_dir.mkdir()
    (payload_dir / "v.json").write_text(json.dumps([payload]))
    assert cli.main(["load", str(payload_dir)]) == 0

    driver = cli_env.get_driver()

    def stored_verdict():
        with driver.session() as session:
            rec = session.run(
                "MATCH (s:Summary {target_id: $t}) RETURN s.semantic_verdict AS v",
                t=CTRL_METHOD,
            ).single()
        return rec["v"] if rec else None

    try:
        first = stored_verdict()
        assert first is not None and json.loads(first) == verdict

        with driver.session() as session:
            session.run("MATCH (s:Summary) DETACH DELETE s")
        assert stored_verdict() is None

        assert cli.main(["load", str(payload_dir)]) == 0
        after = stored_verdict()
    finally:
        driver.close()

    assert after is not None and json.loads(after) == verdict


# --- ac-4: durability — embedding + vector index survive a Neo4j drop + reload --


EMBED_DIM = 1536
# A deterministic, non-zero LITERAL embedding (no model call — the vector rides
# in on the git-tracked payload, provider-free). Components are (k+1)/8, all
# exactly representable as doubles, so the stored value round-trips byte-identical.
_EMBEDDING = [((i % 8) + 1) / 8.0 for i in range(EMBED_DIM)]
EMBEDDING_MODEL = "ext-embed-v1"


def _embedded_summary_dict(target_id, refs):
    d = _summary_dict(target_id, refs)
    d["embedding"] = list(_EMBEDDING)
    d["embedding_model"] = EMBEDDING_MODEL
    d["embedding_dim"] = EMBED_DIM
    return d


def _write_embedded_payload_dir(root):
    """A git-tracked payload dir holding one embedded summary, grounded in real
    ingested fixture nodes."""
    root.mkdir()
    (root / "e.json").write_text(
        json.dumps([_embedded_summary_dict(CTRL_METHOD, [CTRL_METHOD, SVC_METHOD])])
    )
    return root


def _vector_queryable_embedding(driver, target_id):
    """The stored embedding for ``target_id`` iff it is queryable through the
    Summary vector index right now, else None. Awaits ONLINE first; a missing
    index (not yet provisioned) yields None instead of raising, so the assertion
    reads as a clean AC failure rather than a raw traceback."""
    from neo4j.exceptions import ClientError

    from palimpsest.kg.summary import VECTOR_INDEX_NAME

    with driver.session() as session:
        session.run("CALL db.awaitIndexes($t)", t=300)
        try:
            rows = list(
                session.run(
                    "CALL db.index.vector.queryNodes($name, $k, $probe) "
                    "YIELD node WHERE node.target_id = $tid "
                    "RETURN node.embedding AS embedding",
                    name=VECTOR_INDEX_NAME,
                    k=10,
                    probe=list(_EMBEDDING),
                    tid=target_id,
                )
            )
        except ClientError:
            return None
    return rows[0]["embedding"] if rows else None


def test_e2e_embedding_and_vector_index_survive_drop_and_reload(cli_env, tmp_path):
    """ac-4: durability. An embedded summary loaded from a git-tracked payload dir
    is bound AND queryable through the Summary vector index. After a full Neo4j
    DROP (DETACH DELETE every node + DROP the vector index), re-ingesting the
    structural layer and reloading the SAME payload restores the IDENTICAL
    embedding value and re-provisions the vector index (the embedded summary is
    queryable again). Idempotent restore — the vector index rides the load path,
    git = SoT, Neo4j = re-buildable projection."""
    from palimpsest.kg.summary import VECTOR_INDEX_NAME

    assert cli.main(["ingest", "--repo", str(FIXTURES)]) == 0

    payload_dir = _write_embedded_payload_dir(tmp_path / "summaries")
    assert cli.main(["load", str(payload_dir)]) == 0

    driver = cli_env.get_driver()
    try:
        # Bound + queryable through the vector index after the first load.
        before = _vector_queryable_embedding(driver, CTRL_METHOD)
        assert before is not None, (
            "embedded summary not queryable through the vector index after load "
            "(the index was not provisioned on the load path)"
        )
        assert before == _EMBEDDING  # the literal payload vector, bound as-is

        # Full Neo4j drop: wipe EVERY node AND the vector index; keep git SoT.
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            session.run(f"DROP INDEX `{VECTOR_INDEX_NAME}` IF EXISTS")
        assert _vector_queryable_embedding(driver, CTRL_METHOD) is None

        # Rebuild the structural layer + reload the SAME git payload dir.
        assert cli.main(["ingest", "--repo", str(FIXTURES)]) == 0
        assert cli.main(["load", str(payload_dir)]) == 0

        after = _vector_queryable_embedding(driver, CTRL_METHOD)
    finally:
        driver.close()

    assert after is not None, (
        "vector index not restored after the Neo4j drop + git reload"
    )
    assert after == before  # identical embedding value, index restored (idempotent)


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

