"""TDD for the CommunityReport loader (wi_260702smx).

Realizes ADR-20260702-communityreport-load-contract: a CommunityReport is an
externally-generated Summary whose target is a Community node (``community:<sha>``).
Beyond the base Summary contract it enforces MEMBERSHIP-GROUNDING — every claim
``source_ref`` must resolve to a member of the target Community (a member Class,
or a node contained by one). A report ABOUT a community must be grounded in that
community's members, not arbitrary code. Provider-free; live Neo4j (conftest).
"""

import copy

import pytest

from palimpsest.ir import REPO, Summary, SummaryClaim
from palimpsest.kg import augment_communities, ingest, load_summaries

REPORT_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"


@pytest.fixture
def community_ingested(clean_db, ir):
    """A clean DB with the Community-augmented fixture IR ingested once."""
    aug = copy.deepcopy(ir)
    prov = next(n for n in aug.nodes if n.kind == REPO).provenance
    augment_communities(aug, prov)
    ingest(clean_db, aug)
    return clean_db, aug


def _community_with_members(driver):
    """Discover a Community + its member Class ids from the live graph (no
    hardcoding — the fixture partition is derived, not fixed)."""
    with driver.session() as session:
        rec = session.run(
            "MATCH (cls:Class)-[:MEMBER_OF]->(comm:Community) "
            "WITH comm, collect(cls.id) AS members "
            "RETURN comm.id AS cid, members "
            "ORDER BY size(members) DESC, comm.id LIMIT 1"
        ).single()
    return rec["cid"], rec["members"]


def _report(target_cid, claims, **over):
    base = dict(
        target_id=target_cid,
        claims=claims,
        generator="fixture-report-generator",
        model="fixture-model-v1",
        source_commit=REPORT_COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    base.update(over)
    return Summary(**base)


def test_community_report_loads_grounded_in_members(community_ingested):
    """A report targeting a Community, grounded in its member Classes, loads as an
    inferred SUMMARIZES edge to the Community; code_bound_at binds to the
    Community's committed_at (freshness follows code, not the generator)."""
    driver, _ = community_ingested
    cid, members = _community_with_members(driver)
    claims = tuple(
        SummaryClaim(text=f"about {m}", source_refs=(m,)) for m in members
    )
    res = load_summaries(driver, [_report(cid, claims)])
    assert res.loaded == 1 and res.rejected == 0

    with driver.session() as session:
        edge = session.run(
            "MATCH (s:Summary {target_id:$t})-[r:SUMMARIZES]->(c:Community {id:$t}) "
            "RETURN r.edge_kind AS k",
            t=cid,
        ).single()
        commit = session.run(
            "MATCH (c:Community {id:$t}) RETURN c.committed_at AS ca", t=cid
        ).single()["ca"]
        cb = session.run(
            "MATCH (s:Summary {target_id:$t}) RETURN s.code_bound_at AS cb", t=cid
        ).single()["cb"]
    assert edge is not None and edge["k"] == "inferred"
    assert cb == commit


def test_community_report_rejects_non_member_grounding(community_ingested):
    """A report whose claim cites a node OUTSIDE the target community (here a
    Package — resolves but is not a member) is rejected entity-atomically: the
    membership-grounding refinement over the base Summary contract."""
    driver, _ = community_ingested
    cid, members = _community_with_members(driver)
    with driver.session() as session:
        pkg = session.run("MATCH (p:Package) RETURN p.id AS id LIMIT 1").single()["id"]

    bad = _report(
        cid,
        claims=(
            SummaryClaim(text="grounded in a member", source_refs=(members[0],)),
            SummaryClaim(text="grounded outside the community", source_refs=(pkg,)),
        ),
    )
    res = load_summaries(driver, [bad])
    assert res.loaded == 0 and res.rejected == 1
    assert "member" in res.rejections[0].reason.lower()

    with driver.session() as session:
        c = session.run(
            "MATCH (s:Summary {target_id:$t}) RETURN count(s) AS c", t=cid
        ).single()["c"]
    assert c == 0  # atomic: the member-grounded claim did not load either


def test_non_community_summary_unaffected_by_membership_rule(community_ingested):
    """Regression guard: a normal (code-target) Summary is not subjected to the
    community membership rule — it still loads by ordinary ref resolution."""
    driver, aug = community_ingested
    method = next(n for n in aug.nodes if n.kind == "Method")
    res = load_summaries(
        driver,
        [
            _report(
                method.qualified_name,
                claims=(
                    SummaryClaim(text="a method summary", source_refs=(method.qualified_name,)),
                ),
            )
        ],
    )
    assert res.loaded == 1 and res.rejected == 0
