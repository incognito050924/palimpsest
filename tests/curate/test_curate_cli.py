"""AC3 — the `curate` CLI materialises a deterministic, load-schema-valid payload.

The producer's LLM is stubbed (monkeypatch the curate package's ``default_generate``)
so this stays hermetic — no key, no network, no Neo4j. The command's job is the
git-SoT materialisation front-half: run curate, freeze the output to
``<out>/<deterministic-id>.json`` as a JSON array, and leave loading to the
existing ``load`` subcommand.
"""

import json

import palimpsest.curate as curate_pkg
from palimpsest.cli import main
from palimpsest.ir import Summary

TARGET = "com.ecoletree.commute.CommuteController#punchIn()"
KLASS = "com.ecoletree.commute.CommuteController"

LLM_JSON = json.dumps(
    {
        "claims": [
            {"text": "Handles the go-to-work punch-in flow.", "source_refs": [TARGET]},
            {"text": "Coupled to its declaring controller.", "source_refs": [KLASS]},
        ],
        "gap": "Leave/holiday punch variants are not covered.",
        "confidence": 0.82,
    }
)

CURATE_ARGV = [
    "curate",
    "--target", TARGET,
    "--ground", TARGET,
    "--ground", KLASS,
    "--facts", "punchIn() records the go-to-work punch; declared in CommuteController.",
    "--generator", "ditto-curator",
    "--model", "claude-opus-4-8",
    "--source-commit", "c20b7332d8c60ce73794427a4c28120b085c134d",
    "--created-at", "2026-07-12T09:00:00+09:00",
]


def _run_curate(monkeypatch, out_dir):
    monkeypatch.setattr(curate_pkg, "default_generate", lambda prompt, **kw: LLM_JSON)
    return main([*CURATE_ARGV, "--out", str(out_dir)])


def test_curate_cli_writes_one_load_schema_valid_payload(tmp_path, monkeypatch):
    out = tmp_path / "summaries"
    rc = _run_curate(monkeypatch, out)
    assert rc == 0

    files = sorted(out.glob("*.json"))
    assert len(files) == 1, f"expected exactly one materialised payload, got {files}"

    # The file is a JSON ARRAY of summary objects — the exact shape the existing
    # `load` subcommand reads (cli._read_payload_file -> Summary.from_dict).
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    summary = Summary.from_dict(data[0])  # must not raise -> load-schema valid
    assert summary.target_id == TARGET
    assert summary.generator == "ditto-curator"
    assert summary.model == "claude-opus-4-8"
    # content-verdict stays external: the materialised payload carries none.
    assert summary.semantic_verdict is None


def test_curate_cli_is_deterministic(tmp_path, monkeypatch):
    """Same inputs -> same filename and byte-identical content (re-runnable)."""
    out = tmp_path / "summaries"
    _run_curate(monkeypatch, out)
    first = sorted(out.glob("*.json"))
    first_bytes = first[0].read_bytes()

    _run_curate(monkeypatch, out)  # re-run into the same dir
    second = sorted(out.glob("*.json"))
    assert [p.name for p in first] == [p.name for p in second]
    assert len(second) == 1  # no second file — deterministic id overwrites
    assert second[0].read_bytes() == first_bytes
