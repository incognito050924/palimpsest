"""AC1 — reverse isolation boundary for the opt-in generative producer.

The path-scoped probes (tests/recall/*, tests/kg/test_summary.py,
tests/e2e/test_cli_e2e.py) guard the FORWARD direction: importing recall/load
pulls in no generative module. This guards the REVERSE direction: importing
``palimpsest.curate`` pulls in NONE of the recall/load surface.

Together they are the mechanical definition of "isolation" (ADR-20260706
§결정1): curate sits OUTSIDE the recall/load import closure, so a generative
producer can exist without ever tainting the provider-free recall/load path.
Hermetic — a subprocess import, no Neo4j, no network.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# The recall/load surface curate must never pull into its import closure.
# A leak of any of these would put a generative module on the recall/load path
# (transitively) and break the path-scoped probes.
FORBIDDEN_RECALL_LOAD_PREFIXES = [
    "palimpsest.recall",  # the whole recall package (GraphRAG, reconcile, ...)
    "palimpsest.kg",      # load surface: load_summaries / load_risks / ...
    "palimpsest.cli",     # the CLI wires load; curate must not depend on it
]


def test_curate_import_closure_excludes_recall_and_load_surface():
    """Importing palimpsest.curate loads NONE of palimpsest.recall / .kg / .cli."""
    src = Path(__file__).parents[1].parent / "src"
    probe = (
        "import importlib, sys, json\n"
        "importlib.import_module('palimpsest.curate')\n"
        f"forbidden = {FORBIDDEN_RECALL_LOAD_PREFIXES!r}\n"
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
    assert loaded == [], f"curate import closure leaked recall/load modules: {loaded}"
