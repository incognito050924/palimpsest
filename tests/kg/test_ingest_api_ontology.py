"""TDD for the HTTP-API ingest ontology (ac-8, Decisions 3/4/6).

WHY this file exists (background for approver + consumer):

This node lands the SHARED HTTP-API ontology every downstream extractor depends
on. Two load-bearing invariants must hold at the ingest boundary, and both are
checked here PURELY in Python (no live Neo4j — a driver stub asserts if a session
is ever opened, proving the checks fire before any Cypher):

  * ApiCall IS a registered deterministic NODE label (Frozen Invariant 3): so a
    real ApiCall node buckets into ``nodes_by_label`` instead of raising, and
    ``create_constraints`` provisions its uniqueness constraint.
  * CALLS_API is NOT in ``REL_TYPES`` (Frozen Invariant 3): it is an INFERRED
    cross-tier edge with a dedicated loader. The generic writer must never stamp
    it deterministic, so an accidental CALLS_API in ``ir.edges`` is REJECTED by
    the fail-closed guard (``if e.kind not in REL_TYPES: raise``) — that rejection
    is what FORCES use of the dedicated ``kg/calls_api.py`` loader.
  * ``role`` (Decision 6) is a pure PROPERTY OFF identity (is_test/server_only
    precedent): it survives ``to_dict`` and ``scope_to_branch`` but never enters
    ``id``/``branch_scoped_id``.
"""

import pytest

from palimpsest.ir import (
    API_CALL,
    CALLS_API,
    CLASS,
    ENDPOINT,
    Edge,
    IR,
    Node,
    Provenance,
    branch_scoped_id,
    scope_to_branch,
)
from palimpsest.kg.ingest import NODE_LABELS, REL_TYPES, ingest


PROV = Provenance(
    source_commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    author="fixture <fixture@example.com>",
    committed_at="2026-07-13T00:00:00+09:00",
)


class _NoSessionDriver:
    """A driver stub that asserts if ingest ever opens a session — proving the
    fail-closed guard fires BEFORE any Cypher / live DB is touched."""

    def session(self, *args, **kwargs):
        raise AssertionError(
            "ingest reached driver.session() — the fail-closed guard did not fire "
            "before Cypher interpolation"
        )


def test_apicall_registered_in_node_labels():
    """ac-8: ApiCall is a registered deterministic node label (Frozen Invariant 3)
    so it goes through the generic node MERGE + gets a uniqueness constraint."""
    assert API_CALL == "ApiCall"
    assert API_CALL in NODE_LABELS


def test_calls_api_not_in_rel_types():
    """ac-8 / Frozen Invariant 3: CALLS_API is an inferred edge with a dedicated
    loader — it must NEVER be a REL_TYPES member (the generic deterministic writer
    would otherwise stamp it edge_kind='deterministic', laundering the layer)."""
    assert CALLS_API == "CALLS_API"
    assert CALLS_API not in REL_TYPES


def test_accidental_calls_api_edge_is_rejected_fail_closed():
    """ac-8: an accidental CALLS_API edge in ir.edges is REJECTED by the fail-closed
    guard before any DB access. Both endpoints resolve to real IR nodes (a
    registered ApiCall and a registered Endpoint), so the edge reaches the
    REL_TYPES guard — and only its KIND is illegal, forcing the dedicated loader."""
    apicall_qn = "apicall:GET /api/orders/{}"
    endpoint_qn = "spring:GET /api/orders"
    ir = IR(
        nodes=[
            Node(kind=API_CALL, qualified_name=apicall_qn, name="GET",
                 provenance=PROV, path="src/lib/api.ts", start_line=3, end_line=3),
            Node(kind=ENDPOINT, qualified_name=endpoint_qn, name="GET",
                 provenance=PROV, path="src/main/java/OrderController.java"),
        ],
        edges=[Edge(kind=CALLS_API, src=apicall_qn, dst=endpoint_qn,
                    provenance=PROV)],
    )

    with pytest.raises(KeyError):
        ingest(_NoSessionDriver(), ir)


def test_role_round_trips_off_identity():
    """Decision 6: ``role`` is a pure property OFF identity. It rides ``to_dict``
    and is preserved by ``scope_to_branch``'s ``replace``, but never perturbs
    ``id``/``branch_scoped_id`` — a controller and a plain class with the same
    qualified_name still share one id (role is not an identity dimension)."""
    qn = "kr.co.ecoletree.OrderController"
    controller = Node(kind=CLASS, qualified_name=qn, name="OrderController",
                      provenance=PROV, role="controller")

    # role is carried on to_dict, and is OFF identity.
    assert controller.to_dict()["role"] == "controller"
    assert controller.id == qn                       # role does not enter id

    # scope_to_branch preserves role (replace) without folding it into identity.
    scoped = scope_to_branch(IR(nodes=[controller], edges=[]), "feat-a").nodes[0]
    assert scoped.role == "controller"               # preserved by replace
    assert scoped.id == branch_scoped_id("feat-a", qn)  # id = branch + qn only

    # An unmarked node reports role=None (null-drop at ingest, no phantom value).
    plain = Node(kind=CLASS, qualified_name=qn, name="OrderController",
                 provenance=PROV)
    assert plain.role is None
    assert plain.to_dict()["role"] is None
    assert plain.id == controller.id                 # same id despite differing role
