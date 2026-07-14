"""Load the runtime-coverage overlay (COVERS edges) into the KG (wi_260714v6m, issue #19 ②).

palimpsest calls NO LLM and runs NO build. A COVERS edge is an OBSERVED execution fact:
some external producer ran the test suite with per-test coverage attribution (pytest
``--cov-context``, jacoco per-test sessions, c8 per-test, …) OUTSIDE palimpsest and
materialised the result as a producer-neutral payload — for each test, the production
methods it executed at runtime. palimpsest ingests that payload and MERGEs one
``edge_kind='runtime'`` COVERS edge from the test Method to each covered production
Method. This catches the reflection / DI / polymorphic-dispatch callers that the static
CALLS lower bound cannot see (``recall.graphrag._STATIC_LOWER_BOUND_GAP``), AUGMENTING —
never replacing — the static test-impact channel.

Applies ADR-20260706 §결정6's isolated-producer / HEAD-only auxiliary-overlay pattern to
the test-impact axis (ADR-20260714). The three identity invariants are PRESERVED, not
excepted: palimpsest never builds (build-less — the producer measures coverage), the load
is producer-neutral (host-neutral — absent where no coverage payload exists), and the
overlay is HEAD-only (the tree-sitter spine guarantees transitive-history uniformity —
this loader is DELIBERATELY never called inside ``backfill``'s per-commit loop).

Dedicated loader, NOT the generic writer (Frozen Invariant 3). COVERS is deliberately
absent from ``kg.ingest.REL_TYPES``, so this module is the ONLY producer of the edge; the
generic ``_REL_MERGE`` writer (which stamps ``edge_kind='deterministic'``) is never reused.

Grounded load (the summaries / risks / calls_api precedent). Both endpoints are resolved
by ``qualified_name`` before the edge is written; an unresolved TEST endpoint REJECTS the
whole record (the test anchor is meaningless without a node), and an unresolved COVERED
endpoint (coverage naturally spans unmodeled / third-party methods) is SURFACED in the
reasons — never a silent no-op.

Idempotence. Each edge is MERGEd on the ``(test)-[:COVERS]->(prod)`` pattern, so re-running
over an unchanged graph + payload changes nothing (a rebuildable projection).

Freshness. ``source_commit`` is the built HEAD commit the coverage was measured against
(carried on the payload). ``code_bound_at`` binds to the COVERED production method's
``committed_at`` — freshness follows the covered code, mirroring the summaries/risks binding.
"""

from __future__ import annotations

from dataclasses import dataclass

from palimpsest.ir import EDGE_KIND_RUNTIME

# Resolve the TEST endpoint: an ``is_test`` Method by its qualified_name. The is_test
# filter is what makes a COVERS source a TEST (the overlay is test-impact); a payload
# naming a non-test method as the test anchor does not resolve, and its record is rejected.
_RESOLVE_TEST = """
MATCH (t:Method {qualified_name: $qn})
WHERE t.is_test = true
RETURN t.id AS id, t.committed_at AS committed_at
LIMIT 1
"""

# Resolve a COVERED production endpoint: a Method by its qualified_name.
_RESOLVE_PROD = """
MATCH (m:Method {qualified_name: $qn})
RETURN m.id AS id, m.committed_at AS committed_at
LIMIT 1
"""

# Both endpoints pre-resolved, so this MATCH..MATCH..MERGE always materialises.
# edge_kind='runtime' is the schema-enforced no-laundering separation from BOTH the
# deterministic structural layer and the inferred semantic layer.
_COVERS_MERGE = """
MATCH (t:Method {id: $src})
MATCH (m:Method {id: $dst})
MERGE (t)-[r:COVERS]->(m)
SET r.edge_kind     = $edge_kind,
    r.source_commit = $source_commit,
    r.code_bound_at = $code_bound_at
"""


@dataclass(frozen=True)
class CoverageRecord:
    """One test's runtime coverage: the production methods it executed, measured at a
    built HEAD commit by an external producer. ``covered`` and ``test_qualified_name`` are
    in palimpsest's qualified_name convention — mapping a tool's format (lcov / jacoco /
    …) to that convention is the producer's job (host-neutral, ADR-20260712 §결정1c)."""

    test_qualified_name: str
    covered: tuple[str, ...]
    source_commit: str

    @staticmethod
    def from_dict(d: dict) -> "CoverageRecord":
        return CoverageRecord(
            test_qualified_name=d["test_qualified_name"],
            covered=tuple(d.get("covered", ())),
            source_commit=d["source_commit"],
        )


@dataclass(frozen=True)
class CoverageLoadResult:
    """Outcome of a coverage load: records considered, COVERS edges merged, records
    rejected (test endpoint unresolved), and every surfaced reason (test- or covered-
    endpoint unresolved) — nothing dropped silently."""

    intended: int
    loaded: int
    rejected: int
    reasons: tuple[str, ...]


def _resolve(session, query, qn):
    row = session.run(query, qn=qn).single()
    return row.data() if row is not None else None


def load_coverage(driver, records) -> CoverageLoadResult:
    """Load a producer-neutral per-test coverage payload as ``edge_kind='runtime'`` COVERS
    edges. Test-anchored + grounded: an unresolved test rejects its record, an unresolved
    covered method is surfaced (never silent). Idempotent (MERGE on the edge pattern),
    deterministic (edges written in sorted (src, dst) order). Returns the counts + reasons.

    HEAD-only by construction: call this ONCE against the current HEAD projection (its own
    CLI subcommand / post-ingest), NEVER inside ``backfill``'s per-commit loop.
    """
    records = list(records)
    reasons: list[str] = []
    rejected = 0
    edges: dict[tuple[str, str], dict] = {}

    with driver.session() as session:
        for rec in records:
            test = _resolve(session, _RESOLVE_TEST, rec.test_qualified_name)
            if test is None:
                rejected += 1
                reasons.append(
                    f"unresolved test '{rec.test_qualified_name}' (not an is_test Method); "
                    "record rejected"
                )
                continue
            for cov_qn in rec.covered:
                prod = _resolve(session, _RESOLVE_PROD, cov_qn)
                if prod is None:
                    reasons.append(
                        f"unresolved covered method '{cov_qn}' for test "
                        f"'{rec.test_qualified_name}'; edge skipped"
                    )
                    continue
                edges[(test["id"], prod["id"])] = {
                    "src": test["id"],
                    "dst": prod["id"],
                    "edge_kind": EDGE_KIND_RUNTIME,
                    "source_commit": rec.source_commit,
                    "code_bound_at": prod.get("committed_at"),
                }
        for key in sorted(edges):
            session.run(_COVERS_MERGE, **edges[key])

    return CoverageLoadResult(
        intended=len(records),
        loaded=len(edges),
        rejected=rejected,
        reasons=tuple(reasons),
    )
