"""TDD for server-side traversal bounds (fix #6).

``_NEIGHBORS`` and ``_SUMMARIES`` end with ``ORDER BY`` but no Cypher ``LIMIT``,
so a high-degree node streams its *entire* neighbour / summary set to the client
and the budget is applied client-side. ``_neighbors_beyond`` likewise reads every
row just to test existence. These tests pin a server-side ``LIMIT`` (placed AFTER
the existing ``ORDER BY`` so the same rows survive) that bounds the rows read,
while recall results stay equivalent.

Observability: a thin row-counting driver wrapper records how many rows each
Cypher call actually returns from the server. Live Neo4j (see conftest).
"""

from palimpsest.ir import Summary, SummaryClaim
from palimpsest.kg import load_summaries
from palimpsest.recall import expand, recall
from palimpsest.recall.graphrag import DEFAULT_RELATIONS, _neighbors_beyond

CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
CTRL_METHOD = CTRL + "#selectAttedanceCondition(Map,HttpServletRequest)"
MODEL = "fixture-model-v1"
SOURCE_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"

# Substrings that identify each Cypher query in the recorded call log.
_NEIGH = "MATCH (a {id: sid})-[r]-(b)"
_SUMM = "(s:Summary)-[:SUMMARIZES]"


class _CountingResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class _CountingSession:
    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    def run(self, query, **params):
        records = list(self._inner.run(query, **params))
        self._log.append((query, len(records)))
        return _CountingResult(records)


class _CountingDriver:
    """Delegates to a real driver, tallying rows returned per Cypher call."""

    def __init__(self, inner):
        self._inner = inner
        self.log = []

    def session(self, *a, **k):
        return _CountingSession(self._inner.session(*a, **k), self.log)


def _reads(log, needle):
    return [n for q, n in log if needle in q]


def _degree(driver, node):
    with driver.session() as s:
        return len(list(s.run(
            "MATCH (a {id: $id})-[r]-(b) WHERE type(r) IN $rels RETURN DISTINCT b.id",
            id=node, rels=list(DEFAULT_RELATIONS),
        )))


def test_neighbors_read_is_bounded_server_side(recall_db):
    """A high-degree seed does not stream its whole neighbour set; the hop reads
    at most budget + |visited| + 1 rows, and recall results are unchanged."""
    degree = _degree(recall_db, CTRL)
    assert degree > 4  # genuinely more neighbours than the bound below

    drv = _CountingDriver(recall_db)
    out = recall(drv, CTRL, depth=1, limit=3)

    neigh = _reads(drv.log, _NEIGH)
    assert neigh  # the hop actually queried neighbours
    # budget(=limit-1 seed) + |visited|(=1 seed) + 1 = 4 — the full degree never streams.
    assert max(neigh) <= 4
    assert max(neigh) < degree

    # Equivalence: same items in the same order as the unwrapped recall.
    base = recall(recall_db, CTRL, depth=1, limit=3)
    assert [it["id"] for it in out["items"]] == [it["id"] for it in base["items"]]
    assert out["expand_handle"] == base["expand_handle"]


def test_neighbors_beyond_does_not_read_all_rows(recall_db):
    """The frontier existence check reads at most |visited| + 1 rows, not the
    whole neighbour set, and still answers correctly."""
    degree = _degree(recall_db, CTRL)
    assert degree > 2

    drv = _CountingDriver(recall_db)
    visited = {CTRL}
    assert _neighbors_beyond(drv, [CTRL], visited, DEFAULT_RELATIONS) is True

    neigh = _reads(drv.log, _NEIGH)
    assert neigh
    assert max(neigh) <= len(visited) + 1
    assert max(neigh) < degree


def test_summaries_read_is_bounded_server_side(recall_db):
    """A node with many summaries does not stream the whole summary set; the
    summaries query reads at most the recall limit."""
    n = 30
    summaries = [
        Summary(
            target_id=CTRL_METHOD,
            claims=(SummaryClaim(text=f"claim {i}", source_refs=(CTRL_METHOD,)),),
            generator=f"gen-{i}", model=MODEL, source_commit=SOURCE_COMMIT,
            created_at="2026-07-01T09:00:00+09:00",
        )
        for i in range(n)
    ]
    res = load_summaries(recall_db, summaries)
    assert res.loaded == n, res.rejections
    try:
        with recall_db.session() as s:
            full = len(list(s.run(
                "MATCH (sm:Summary)-[:SUMMARIZES]->({id: $a}) WITH DISTINCT sm "
                "MATCH (sm)-[:SUMMARIZES]->(g) RETURN g.id", a=CTRL_METHOD,
            )))
        assert full > 25  # more summary rows than the recall limit below

        drv = _CountingDriver(recall_db)
        recall(drv, CTRL_METHOD, depth=1, limit=25)

        summ = _reads(drv.log, _SUMM)
        assert summ  # the summaries channel was queried
        assert max(summ) <= 25          # server-side bound = recall limit
        assert max(summ) < full         # the full summary set never streams
    finally:
        with recall_db.session() as s:
            s.run(
                "MATCH (s:Summary) WHERE s.generator STARTS WITH 'gen-' "
                "DETACH DELETE s"
            )
