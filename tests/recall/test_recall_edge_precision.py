"""TDD for the edge-precision recall channel: the ``resolution='name'`` signal.

A detect-only, global read-only channel that CONSUMES the per-edge ``resolution``
marker (kg/ingest.py projects it onto CALLS / DEPENDS_ON) and surfaces the
name-resolved (low-precision) Java edges as its own grounded query surface —
mirroring the sibling MODIFIES channels (recall_churn / recall_cochange).

Two tiers (mirrors the sibling recall suites):

* **Mock-unit** — a fake driver returning canned rows, no Neo4j. Pins the
  DETERMINISM contract (ac-1: id-ordered + LIMIT-bounded, ``$lim`` the sole
  parameter, ``.java`` / ``name`` dev literals never interpolated), the
  GROUNDED-not-content-verdict projection + author non-leak (ac-2, incl. the
  DEPENDS_ON-vs-CALLS distinction), and the predate-empty re-ingest gap (ac-3
  folded finding).
* **Live-Neo4j** — the ``recall_db`` fixture (the real ``commute`` Java slice,
  ingested via the real ``ingest``). Proves the signal is NON-DEAD: it returns
  flagged real Java edges (resolution='name') on an ingested corpus (ac-3), and
  never leaks an author.
"""

import json

from palimpsest.recall import recall, recall_edge_precision
from palimpsest.recall.graphrag import (
    _EDGE_PRECISION,
    _EDGE_PRECISION_GAP,
)

RESULT_KEYS = {
    "items", "sources", "summaries", "risks", "decisions", "relations",
    "gaps", "confidence", "expand_handle",
}

# A distinctive author string — the channel must NEVER surface it (edges carry an
# author, but only scalar endpoint projections are returned, so it cannot leak).
SECRET = "SECRET <secret.person@corp.example>"

CTRL = "src/main/java/kr/co/ecoletree/service/commute/controller/CommuteController.java"


# --- fake driver (mock-unit tier, no Neo4j) -----------------------------------

class _FakeRecord:
    def __init__(self, data):
        self._data = data

    def data(self):
        return self._data


class _FakeResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)


class _FakeSession:
    def __init__(self, rows, log):
        self._rows, self._log = rows, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self._log.append((query, params))
        return _FakeResult([_FakeRecord(dict(r)) for r in self._rows])


class _FakeDriver:
    def __init__(self, rows):
        self._rows, self.log = rows, []

    def session(self, *a, **k):
        return _FakeSession(self._rows, self.log)


def _row(id_, relation_type, dst):
    """A canned scalar-projection row, shaped exactly like the query's RETURN. It
    carries NO author key — the query never projects one (endpoint scalars only)."""
    return {
        "id": id_,
        "labels": ["Method"] if relation_type == "CALLS" else ["Class"],
        "name": id_.rsplit(".", 1)[-1],
        "qualified_name": id_,
        "path": CTRL,
        "start_line": 10,
        "end_line": 20,
        "source_commit": "c0ffee",
        "committed_at": "2025-09-03T16:22:54+09:00",
        "relation_type": relation_type,
        "resolution": "name",
        "dst": dst,
    }


# --- mock-unit: determinism (ac-1) --------------------------------------------

def test_query_is_parameterized_and_ordered():
    """ac-1: the Cypher is id-ordered + LIMIT-bounded, ``$lim`` is the SOLE
    parameter, and ``.java`` / ``name`` are dev literals baked into the query text
    (never interpolated), so no untrusted value can reach Cypher."""
    q = _EDGE_PRECISION
    assert "ORDER BY id" in q            # total order -> rebuild-deterministic
    assert "LIMIT $lim" in q             # server-side bound, parameterized
    assert q.count("$") == 1             # $lim is the ONLY parameter
    assert "'.java'" in q and "'name'" in q   # dev literals, inlined not formatted
    assert "{" not in q and "}" not in q      # no str.format interpolation slot
    # Scalar projections only — never a whole-node RETURN (no author leak surface).
    assert "author" not in q
    assert " AS id" in q and " AS relation_type" in q


def test_items_preserve_query_order_and_pass_lim():
    """ac-1: items map 1:1 to the (already id-ordered) query rows, and the caller
    ``limit`` is forwarded as the ``$lim`` parameter."""
    rows = [_row("a.f", "CALLS", "x.g"), _row("b.h", "DEPENDS_ON", "y")]
    drv = _FakeDriver(rows)
    out = recall_edge_precision(drv, limit=7)
    assert set(out) == RESULT_KEYS
    assert [it["id"] for it in out["items"]] == ["a.f", "b.h"]
    # the sole param carried to Cypher is lim (nothing untrusted interpolated)
    assert drv.log and drv.log[0][1] == {"lim": 7}


# --- mock-unit: grounded, not a content-verdict; no author leak (ac-2) --------

def test_items_grounded_no_verdict_no_author_leak():
    """ac-2: each flag is a GROUNDED observation (source + relation + resolution),
    NOT a content/quality verdict, and no author string ever surfaces."""
    rows = [_row("a.f", "CALLS", "x.g")]
    out = recall_edge_precision(_FakeDriver(rows), limit=25)
    it = out["items"][0]
    # grounded provenance
    assert it["sources"]["source_commit"] and it["sources"]["path"]
    assert it["sources"]["start_line"] is not None
    # carries the marker it consumed, never re-deriving precision
    assert it["resolution"] == "name"
    assert it["relation"] == "CALLS"
    # NO content-verdict field of any kind
    for banned in ("verdict", "quality", "score", "severity", "defect"):
        assert banned not in it
    # confidence is deterministic grounding coverage (all grounded -> 1.0), not a model score
    assert out["confidence"] == 1.0
    # author non-leak — nowhere in the serialized result
    blob = json.dumps(out)
    assert "author" not in blob and SECRET not in blob


def test_gap_distinguishes_depends_on_from_calls():
    """ac-2: the standing gap distinguishes the STRUCTURALLY-always-low DEPENDS_ON
    from a MEANINGFUL CALLS name-fallback, and never claims completeness. The
    per-item ``relation`` also partitions the two kinds."""
    rows = [_row("a.f", "CALLS", "x.g"), _row("b.h", "DEPENDS_ON", "y")]
    out = recall_edge_precision(_FakeDriver(rows), limit=25)
    relations = {it["relation"] for it in out["items"]}
    assert relations == {"CALLS", "DEPENDS_ON"}   # both kinds distinguishable
    gap = _EDGE_PRECISION_GAP
    assert gap in out["gaps"]
    assert "DEPENDS_ON" in gap and "CALLS" in gap
    assert "not" in gap.lower() and "complet" in gap.lower()   # completeness NOT claimed


# --- mock-unit: predate-empty is a DISTINCT gap (ac-3 folded finding) ----------

def test_empty_result_emits_predate_reingest_gap():
    """ac-3: an EMPTY result gets a DISTINCT predate/re-ingest advisory ON TOP of the
    standing gap — a bare 'no low-precision edges' would be a FALSE all-clear for a
    graph ingested before the marker landed (edges with no resolution property)."""
    out = recall_edge_precision(_FakeDriver([]), limit=25)
    assert out["items"] == []
    assert out["confidence"] == 0.0
    assert _EDGE_PRECISION_GAP in out["gaps"]            # standing gap still present
    predate = [g for g in out["gaps"] if "predate" in g.lower()]
    assert predate                                       # DISTINCT advisory added
    assert "re-ingest" in predate[0].lower()
    assert len(out["gaps"]) == 2                          # clean-empty distinguished from predate


# --- live-Neo4j: the signal is non-dead on a real ingested corpus (ac-3) -------

def test_live_flags_real_java_name_resolved_edges(recall_db):
    """ac-3 (non-dead): on the ingested ``commute`` Java slice the channel returns
    flagged REAL edges — resolution='name' on .java endpoints (Java DEPENDS_ON is
    always name-resolved) — and never leaks an author. With items present, the
    predate advisory is absent (clean, not predate)."""
    out = recall_edge_precision(recall_db, limit=100)
    assert set(out) == RESULT_KEYS
    assert out["items"], "expected name-resolved Java edges on the ingested corpus"
    for it in out["items"]:
        assert it["sources"]["path"].endswith(".java")   # .java endpoints only
        assert it["relation"] in ("CALLS", "DEPENDS_ON")
        assert it["resolution"] == "name"
    # author non-leak on the real graph (edges DO carry an author)
    blob = json.dumps(out)
    assert "author" not in blob and "@ecoletree.com" not in blob
    # clean (non-empty) -> the predate advisory is NOT emitted
    assert not any("predate" in g.lower() for g in out["gaps"])
    assert _EDGE_PRECISION_GAP in out["gaps"]


def test_live_edge_precision_not_reachable_via_default_recall(recall_db):
    """The resolution marker is an EDGE property, not a node — ordinary recall over
    the seed never turns it into an item; the channel is the only surface for it."""
    out = recall(recall_db, CTRL, depth=2, limit=50)
    for it in out["items"]:
        assert "resolution" not in it
