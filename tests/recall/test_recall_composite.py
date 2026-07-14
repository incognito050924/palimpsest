"""TDD for the composite refactor-candidate identifier: facet-1 ∧ facet-2.

The DETERMINISTIC, provider-free selector (facet-3 / wi_260714ns9 M1+M3) that
COMPOSES the two Relate signals in ONE id-ordered query: per Java caller Method it
computes BOTH the name-resolution axis (name_resolved CALLS — low precision) AND the
locality axis (cross-package typed CALLS — low locality), and keeps ONLY the Methods
that carry BOTH (``cross > 0 AND name_calls > 0``). It emits, per surviving Method,
an EXTRACTION TARGET tuple — ``(target_id, grounding_ids, neutral facts triple)`` —
for the host-injected LLM producer to synthesise a grounded co-occurrence
OBSERVATION over (never a refactor verdict, never a SELECTION — the selection is
this deterministic query, F-Q6).

Two tiers (mirrors the sibling recall suites):

* **Mock-unit** — a fake driver returning canned aggregated rows, no Neo4j. Pins the
  DETERMINISM contract (id-ordered + LIMIT-bounded, ``$lim`` the SOLE parameter,
  ``.java`` / ``name`` / ``typed`` dev literals never interpolated, the composite
  ``cross>0 AND name_calls>0`` predicate in the query), the EXTRACTION-TARGET
  projection (target + grounding_ids + neutral facts triple, author non-leak, no
  verdict), and the empty-vs-marker-absent honest gap.
* **Live-Neo4j** — the ``recall_db`` fixture (the real ``commute`` Java slice). The
  DOCUMENTED VACUITY witness: on this corpus the composite is EMPTY because the
  ``1b1e90d`` precision improvement resolved every CALLS edge to ``typed`` (zero
  name_resolved CALLS), so the two axes never co-occur on one Method. The mechanism
  runs and honestly emits the absent-marker gap — it does NOT fabricate a candidate.
"""

import json

from palimpsest.recall import recall_refactor_candidates
from palimpsest.recall.graphrag import (
    _REFACTOR_CANDIDATES,
    _REFACTOR_CANDIDATES_GAP,
)

RESULT_KEYS = {
    "items", "sources", "summaries", "risks", "decisions", "relations",
    "gaps", "confidence", "expand_handle",
}

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


def _row(id_, cross, same, unresolved, name_calls, grounding):
    """A canned aggregated row shaped exactly like the query's RETURN. Carries NO
    author key — the query never projects one (endpoint scalars only)."""
    return {
        "id": id_,
        "labels": ["Method"],
        "name": id_.rsplit("#", 1)[-1],
        "qualified_name": id_,
        "path": CTRL,
        "start_line": 10,
        "end_line": 40,
        "source_commit": "c0ffee",
        "committed_at": "2025-09-03T16:22:54+09:00",
        "cross": cross,
        "same": same,
        "unresolved": unresolved,
        "name_calls": name_calls,
        "grounding": list(grounding),
    }


# --- mock-unit: determinism + composite predicate (M1/M3) ---------------------

def test_query_is_parameterized_ordered_and_composite():
    """The Cypher is id-ordered + LIMIT-bounded, ``$lim`` is the SOLE parameter,
    ``.java`` / ``name`` / ``typed`` are dev literals (never interpolated), and it
    computes BOTH axes then filters to the composite (``cross>0 AND name_calls>0``)
    in ONE query — never an intersection of two independently-capped recalls (M3)."""
    q = _REFACTOR_CANDIDATES
    assert "ORDER BY id" in q            # total order -> rebuild-deterministic
    assert "LIMIT $lim" in q             # server-side bound, parameterized
    assert q.count("$") == 1             # $lim is the ONLY parameter
    assert "'.java'" in q and "'name'" in q and "'typed'" in q  # dev literals inlined
    assert "{" not in q and "}" not in q      # no str.format interpolation slot
    assert "author" not in q                  # scalar projections only, no author leak
    # the COMPOSITE predicate: BOTH axes required on one Method (single query)
    assert "cross > 0" in q and "name_calls > 0" in q
    # variable-length CONTAINS climb (nested classes add hops), never a fixed path
    assert "[:CONTAINS*]" in q


def test_emits_extraction_targets_target_grounding_facts():
    """Each surviving Method is emitted as an EXTRACTION TARGET: the target id, a
    grounding set that includes the target plus its cited callees, and a NEUTRAL
    facts triple — exactly what the producer needs to build a CurateRequest (M1)."""
    rows = [
        _row("a.C#m()", cross=2, same=1, unresolved=0, name_calls=1, grounding=["x.S#f()"]),
        _row("b.C#n()", cross=1, same=0, unresolved=0, name_calls=2, grounding=["y.T#g()"]),
    ]
    drv = _FakeDriver(rows)
    out = recall_refactor_candidates(drv, limit=7)
    assert set(out) == RESULT_KEYS
    assert [it["id"] for it in out["items"]] == ["a.C#m()", "b.C#n()"]
    it = out["items"][0]
    # grounding_ids: the target itself is always citable, plus its callees
    assert it["grounding_ids"][0] == "a.C#m()"
    assert "x.S#f()" in it["grounding_ids"]
    # neutral facts triple: the raw counts + resolution marker, NO adjectives
    facts = it["facts"]
    assert "2" in facts and "1" in facts   # cross=2, name_calls=1 present as counts
    for adjective in ("high", "low", "poor", "bad", "should", "refactor", "smell", "violation"):
        assert adjective not in facts.lower()
    # the sole param carried to Cypher is lim (nothing untrusted interpolated)
    assert drv.log and drv.log[0][1] == {"lim": 7}


def test_items_grounded_no_verdict_no_author_leak():
    """Each candidate is a GROUNDED extraction target (source + neutral counts), NOT a
    content/quality verdict, and no author string ever surfaces (F-Q6 framing)."""
    rows = [_row("a.C#m()", cross=1, same=0, unresolved=0, name_calls=1, grounding=["x.S#f()"])]
    out = recall_refactor_candidates(_FakeDriver(rows), limit=25)
    it = out["items"][0]
    assert it["sources"]["source_commit"] and it["sources"]["path"]
    assert it["sources"]["start_line"] is not None
    for banned in ("verdict", "quality", "score", "severity", "defect", "recommendation"):
        assert banned not in it
    assert out["confidence"] == 1.0   # all grounded -> deterministic coverage, not a model score
    blob = json.dumps(out)
    assert "author" not in blob and SECRET not in blob


def test_empty_result_emits_absent_marker_gap():
    """An EMPTY result gets a DISTINCT absent/predate advisory ON TOP of the standing
    gap: a bare empty is ambiguous — genuinely no composite (clean) vs a graph that
    predates the resolution/CONTAINS spine. Mirrors the sibling facets' honest gap."""
    out = recall_refactor_candidates(_FakeDriver([]), limit=25)
    assert out["items"] == []
    assert out["confidence"] == 0.0
    assert _REFACTOR_CANDIDATES_GAP in out["gaps"]        # standing gap still present
    absent = [g for g in out["gaps"] if "predate" in g.lower() or "absent" in g.lower()]
    assert absent                                          # DISTINCT advisory added
    assert len(out["gaps"]) == 2                           # clean-empty distinguished


# --- live-Neo4j: DOCUMENTED VACUITY — mechanism runs, empty, honest (M1/M3) ----

def test_live_composite_is_empty_with_honest_gap_on_commute(recall_db):
    """DOCUMENTED VACUITY (facet-3 finding): on the ``commute`` slice the composite is
    EMPTY — the 1b1e90d precision improvement resolved every CALLS to ``typed`` (zero
    name_resolved CALLS), so low-precision-CALLS and low-locality never co-occur on
    one Method. The mechanism RUNS and honestly emits the absent-marker gap; it does
    NOT fabricate a candidate. If this ever becomes non-empty, a witness corpus has
    appeared (follow-up) — update this test then, do not weaken it now."""
    out = recall_refactor_candidates(recall_db, limit=100)
    assert set(out) == RESULT_KEYS
    # every surviving item (should be none here) would be grounded + author-free
    for it in out["items"]:
        assert it["sources"]["path"].endswith(".java")
        assert it["grounding_ids"][0] == it["id"]
    # the documented current-corpus fact: no composite witness
    assert out["items"] == [], (
        "composite became non-empty — a name_resolved-CALLS witness corpus appeared; "
        "this is the follow-up trigger, update the test to assert the witness"
    )
    assert out["confidence"] == 0.0
    assert _REFACTOR_CANDIDATES_GAP in out["gaps"]
    assert any("predate" in g.lower() or "absent" in g.lower() for g in out["gaps"])
    blob = json.dumps(out)
    assert "author" not in blob and "@ecoletree.com" not in blob
