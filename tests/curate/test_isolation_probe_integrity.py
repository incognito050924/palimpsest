"""AC6 — the path-scoped isolation probes actually catch a leak (anti-phantom-green).

The 5 existing probes (tests/recall/test_recall.py, test_recall_semantic.py,
test_reconcile.py, tests/kg/test_summary.py, tests/e2e/test_cli_e2e.py) already
assert the recall/load closures import no generative module; running them in the
suite with curate present is the "green with curate installed" evidence. This
file adds what those cannot show on their own:

  (a) importing ``palimpsest.curate`` FIRST does not leak a generative module into
      any recall/load closure — curate's mere presence stays isolated; and
  (b) a NEGATIVE control: when a forbidden generative module IS in the closure
      (what a real leak would produce), the SAME probe logic turns red — so the
      green in (a) is real, not vacuous.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).parents[2] / "src"

# Same forbidden set the recall/load probes use.
FORBIDDEN_GENERATIVE_MODULES = [
    "openai", "anthropic", "langchain", "langchain_core", "llama_index",
    "transformers", "cohere", "neo4j_graphrag", "vertexai",
    "google.generativeai", "litellm", "sentence_transformers",
]

# The distinct recall/load import targets guarded by the 5 path-scoped probes.
RECALL_LOAD_IMPORTS = [
    "palimpsest.recall",
    "palimpsest.recall.graphrag",
    "palimpsest.kg",
    "palimpsest.cli",
]


def _detect(preamble: str) -> list:
    """Run the probe's detection logic in a fresh interpreter after ``preamble``
    and return the forbidden modules found in the import closure."""
    probe = (
        "import importlib, sys, json, types\n"
        f"{preamble}\n"
        f"forbidden = {FORBIDDEN_GENERATIVE_MODULES!r}\n"
        "bad = sorted({m for m in sys.modules for p in forbidden "
        "if m == p or m.startswith(p + '.')})\n"
        "print(json.dumps(bad))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_probes_stay_green_with_curate_imported_first():
    """(a) curate present in the interpreter leaks no generative module into any
    recall/load closure."""
    for target in RECALL_LOAD_IMPORTS:
        preamble = (
            "importlib.import_module('palimpsest.curate')\n"
            f"importlib.import_module({target!r})"
        )
        assert _detect(preamble) == [], (
            f"importing palimpsest.curate leaked a generative module into {target}"
        )


def test_probe_turns_red_on_injected_leak():
    """(b) negative control: a forbidden module actually present in the recall
    closure is caught — proving the probe is not phantom-green."""
    preamble = (
        "sys.modules['anthropic'] = types.ModuleType('anthropic')\n"
        "importlib.import_module('palimpsest.recall')"
    )
    caught = _detect(preamble)
    assert "anthropic" in caught, (
        "probe failed to flag an injected generative module — it would not catch "
        "a real isolation leak either"
    )
