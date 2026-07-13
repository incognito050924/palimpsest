"""ac-6 (coverage sweep, wi_260713iah) — the CALLS_API static-lower-bound disclosure
must EXHAUSTIVELY name every incompleteness cause, so a consumer never reads an empty
CALLS_API result as "completeness".

WHY these tests exist (background for approver + consumer):

wi_260713iah widens cross-tier CALLS_API extraction (in-house HTTP wrappers, JVM S2S
@Value base-urls, vite/svelte dev-proxy rewrites, one-hop dataflow). Each new matcher
introduces a NEW way a link can be legitimately MISSING. Following the #18
exhaustive-enumeration precedent (commit b565132 — mechanical under-fill named
alongside the semantic invisibles), the honest gap-reason string must NAME these new
causes, not leave them silently absent. A pure-string enumeration assertion (no Neo4j):
the disclosure is a module-level constant.

Original two causes (dynamic-URL -> no ApiCall; unmatched route -> no edge) must STILL
be named — the widening only adds causes, never drops the incumbent disclosure.
"""

from palimpsest.recall.api_links import _CROSS_TIER_STATIC_LOWER_BOUND_GAP


def test_gap_still_names_incumbent_two_causes():
    """The two pre-existing causes remain named — the enumeration only grows."""
    assert "dynamic" in _CROSS_TIER_STATIC_LOWER_BOUND_GAP      # dynamic URL -> no ApiCall
    assert "matches no Endpoint" in _CROSS_TIER_STATIC_LOWER_BOUND_GAP  # unmatched route


def test_gap_names_in_house_wrapper_skip():
    """(1) Calls via project-local HTTP wrappers are recognized-gap by design
    (ADR-20260713), not "no call" — the disclosure names the wrapper skip."""
    assert "wrapper" in _CROSS_TIER_STATIC_LOWER_BOUND_GAP


def test_gap_names_unresolved_value_base_url():
    """(2) A JVM S2S caller whose config base-url couldn't be resolved (env-only /
    ambiguous profile / compose-alias) yields no grounded target — named as a gap."""
    assert "@Value" in _CROSS_TIER_STATIC_LOWER_BOUND_GAP
    assert "base-url" in _CROSS_TIER_STATIC_LOWER_BOUND_GAP


def test_gap_names_unparsed_dev_proxy():
    """(3) A vite/svelte dev-proxy whose rewrite is a JS function / env-only target
    couldn't be statically evaluated — the unresolved f/e proxy route is named."""
    assert "proxy" in _CROSS_TIER_STATIC_LOWER_BOUND_GAP


def test_gap_names_dataflow_not_recovered():
    """(4) A JVM caller whose URL is assembled / multi-hop (beyond one-hop param->uri)
    yields no ApiCall — the un-recovered dataflow is named."""
    assert "dataflow" in _CROSS_TIER_STATIC_LOWER_BOUND_GAP


def test_gap_never_claims_completeness():
    """Invariant (ac-6): the widened disclosure is STILL a lower bound — it must not
    claim completeness."""
    assert "Completeness is not claimed" in _CROSS_TIER_STATIC_LOWER_BOUND_GAP
