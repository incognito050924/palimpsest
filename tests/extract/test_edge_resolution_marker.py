"""Per-edge resolution-precision marker (ac-1 + constraint C).

WHY these tests exist (AC-1): a KG ``Edge`` must carry a per-edge
``resolution`` PROPERTY ("typed" | "name" | None) that is orthogonal to
``edge_kind``, and it must survive IR (de)serialization AND flow through the
REAL Neo4j projection wiring — not merely ``to_dict``. The marker distinguishes
type-resolved reference edges from name-only (best-effort) ones, so a later
recall node can weight edge trust.

Edge clauses pinned here:
  - to_dict/from_dict round-trip preserves resolution (typed/name/None).
  - a legacy payload (no "resolution" key) loads conservatively as "name"
    (a pre-marker edge was in practice name-resolved; never silently null).
  - _edge_row (the projection row builder) emits the marker ONLY for the
    name-resolved kinds CALLS/DEPENDS_ON (constraint C1), and None for a
    structural kind like CONTAINS (constraint C2) so Neo4j drops the property.
  - _REL_MERGE SETs the marker PER ROW (r.resolution = row.resolution), not as
    a query-level param — it varies per edge (constraint C1).
"""

from palimpsest.ir import Edge, Provenance, CALLS, DEPENDS_ON, CONTAINS
from palimpsest.kg.ingest import _edge_row, _REL_MERGE

PROV = Provenance(source_commit="c0ffee", author="a@b.c", committed_at="2026-07-14T00:00:00Z")


def _edge(kind, resolution):
    return Edge(kind=kind, src="A", dst="B", provenance=PROV, resolution=resolution)


# --- IR (de)serialization symmetry -----------------------------------------

def test_to_dict_carries_resolution():
    d = _edge(CALLS, "typed").to_dict()
    assert d["resolution"] == "typed"


def test_round_trip_preserves_resolution():
    for res in ("typed", "name", None):
        e = _edge(CALLS, res)
        rebuilt = Edge.from_dict(e.to_dict())
        assert rebuilt == e
        assert rebuilt.resolution == res


def test_legacy_payload_without_resolution_loads_as_name():
    # A pre-marker payload has no "resolution" key -> conservative "name".
    legacy = {
        "kind": CALLS,
        "src": "A",
        "dst": "B",
        "provenance": PROV.to_dict(),
    }
    assert Edge.from_dict(legacy).resolution == "name"


# --- REAL projection wiring (constraint C) ---------------------------------

def test_edge_row_carries_resolution_for_name_resolved_kinds():
    # C1: CALLS/DEPENDS_ON carry their per-edge resolution through the row builder.
    assert _edge_row(_edge(CALLS, "typed"))["resolution"] == "typed"
    assert _edge_row(_edge(DEPENDS_ON, "name"))["resolution"] == "name"


def test_edge_row_drops_resolution_for_structural_kinds():
    # C2: a structural kind projects None (Neo4j drops the property) even if a
    # value is (defensively) present on the edge.
    assert _edge_row(_edge(CONTAINS, "typed"))["resolution"] is None


def test_rel_merge_sets_resolution_per_row():
    # C1: the SET must be per-row (row.resolution), not a query-level $param.
    # Whitespace-tolerant: the query aligns "=" with padding.
    normalized = " ".join(_REL_MERGE.split())
    assert "r.resolution = row.resolution" in normalized
