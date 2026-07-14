"""Progressive, grounded, combinatorial recall over the Knowledge Graph.

Design (ac-2 recall + ac-3 honesty):

* **Seed resolution.** A query is a node ``id`` — a symbol ``qualified_name`` or
  a repo-relative file path (a File node's id *is* its path). Both resolve by a
  single ``MATCH (n {id: $id})``. No natural-language parsing in v1.

* **Progressive, bounded traversal.** Breadth-first over the deterministic
  ontology (CALLS / DEPENDS_ON / CONTAINS / IMPORTS), traversed *undirected* so a
  Method reaches its containing Class (an incoming CONTAINS) as well as its
  callees (outgoing CALLS). Two budgets bound it: ``depth`` (max hops) and
  ``limit`` (max items). The whole graph is never loaded; when the budget is
  spent the unexpanded frontier is returned as an ``expand_handle`` so the next
  hop can be pulled on demand via :func:`expand`.

* **Grounding.** Every recalled item carries ``sources`` = ``source_commit`` +
  ``path`` + ``start_line`` / ``end_line`` (the same provenance the KG stamped).
  The top-level ``sources`` list is the same grounding, kept as a *separate*
  channel.

* **Combinatorial only (ac-3).** Assembly is pure graph traversal + dict
  building. There is no LLM / generative call anywhere on this path. Output
  fields stay SEPARATED —
  ``{items, sources, summaries, gaps, confidence, expand_handle}``,
  never a merged prose "answer".

* **Inferred summaries as a separate channel.** Externally-generated summaries
  (the inferred layer, loaded elsewhere — palimpsest never calls an LLM) surface
  in their own ``summaries`` channel, NEVER merged into ``items``. SUMMARIZES is
  not a traversable relation: the structural whitelist (CALLS / DEPENDS_ON /
  CONTAINS / IMPORTS) is the only thing recall walks, so a Summary can never leak
  into the grounded ``items``. The channel is bounded by the same depth/limit
  budget (it is keyed off the already-budgeted recalled node ids), each entry
  carrying its grounding refs, bound commit (``code_bound_at``) and the inferred
  ``edge_kind`` marker.

* **Gaps (ac-3 honesty).** If the seed does not resolve, or an *explicitly
  requested* relation has no edges on the seed, that is stated as an explicit
  gap rather than returned as a confident empty answer. Structural coupling is
  never presented as a risk / quality judgment.
"""

from __future__ import annotations

import json
import math
from datetime import datetime

from palimpsest.ir import (
    CALLS,
    COVERS,
    DEPENDS_ON,
    CONTAINS,
    IMPORTS,
    MEMBER_OF,
    MODIFIES,
    INFERRED_RELATION_TYPES,
    EDGE_KIND_RUNTIME,
    EMBEDDING_DIM,
)
from palimpsest.kg.summary import VECTOR_INDEX_NAME

# The relations recall may traverse (the deterministic structural ontology).
DEFAULT_RELATIONS = (CALLS, DEPENDS_ON, CONTAINS, IMPORTS)

# Namespace prefix of a DesignDecision id (mirrors kg/decision.py): a DECIDES target
# with this prefix is another decision, not code, so it carries no code committed_at.
_DECISION_NS = "decision:"

# Ontology node labels, in the order we pick a node's primary kind.
_NODE_LABELS = ("Repo", "Package", "File", "Class", "Method", "Episode", "Community")

# A query id is label-free, but the uniqueness CONSTRAINT is per (label, id), so
# two nodes CAN share an id under different labels. Order by label before LIMIT 1
# so resolution is deterministic and rebuild-stable (lexicographically-smallest
# label wins) instead of Neo4j's arbitrary scan order.
_RESOLVE = (
    "MATCH (n {id: $id}) RETURN n, labels(n) AS labels "
    "ORDER BY head(labels(n)) LIMIT 1"
)

# One undirected hop from a set of frontier ids over the requested relations.
# DISTINCT rows; deterministic order so item selection under a limit is stable.
_NEIGHBORS = """
UNWIND $ids AS sid
MATCH (a {id: sid})-[r]-(b)
WHERE type(r) IN $rels
RETURN DISTINCT b.id AS id, type(r) AS relation, labels(b) AS labels,
       b.name AS name, b.qualified_name AS qualified_name,
       b.path AS path, b.start_line AS start_line, b.end_line AS end_line,
       b.source_commit AS source_commit, b.committed_at AS committed_at
ORDER BY id, relation
LIMIT $lim
"""

# Which of the requested relations actually have an edge on the seed.
_SEED_REL_TYPES = (
    "MATCH (a {id: $id})-[r]-() WHERE type(r) IN $rels "
    "RETURN DISTINCT type(r) AS relation"
)

# The inferred semantic layer, as a SEPARATE channel. For the recalled node ids
# (already depth/limit bounded), pull each attached Summary plus the code spans it
# grounds to. Bounded by construction — never the whole graph's summaries. One row
# per (summary, grounded ref); ``edge_kind`` rides in as the inferred marker.
_SUMMARIES = """
UNWIND $ids AS anchor_id
MATCH (s:Summary)-[:SUMMARIZES]->({id: anchor_id})
WITH DISTINCT s
MATCH (s)-[r:SUMMARIZES]->(g)
RETURN s.id AS id, s.target_id AS target_id, s.claims AS claims,
       s.code_bound_at AS code_bound_at, s.semantic_verdict AS semantic_verdict,
       s.coverage_verdict AS coverage_verdict,
       r.edge_kind AS edge_kind,
       g.id AS ref_id, g.source_commit AS source_commit, g.path AS path,
       g.start_line AS start_line, g.end_line AS end_line,
       g.committed_at AS committed_at
ORDER BY id, ref_id
LIMIT $lim
"""


def _kind(labels) -> str | None:
    labels = list(labels or ())
    for known in _NODE_LABELS:
        if known in labels:
            return known
    return labels[0] if labels else None


def _sources(rec) -> dict:
    return {
        "source_commit": rec.get("source_commit"),
        "path": rec.get("path"),
        "start_line": rec.get("start_line"),
        "end_line": rec.get("end_line"),
        "committed_at": rec.get("committed_at"),
    }


def _item(rec, relation, depth) -> dict:
    return {
        "id": rec.get("id"),
        "kind": _kind(rec.get("labels")),
        "name": rec.get("name"),
        "qualified_name": rec.get("qualified_name"),
        "relation": relation,   # how it was reached (None for the seed)
        "depth": depth,
        "sources": _sources(rec),
    }


def _is_grounded(item) -> bool:
    s = item["sources"]
    return bool(s["source_commit"] and s["path"] and s["start_line"] is not None)


def _confidence(items) -> float:
    """Deterministic grounding coverage: the share of returned items that
    resolve to concrete code (commit + file:line). Combinatorial, not a model
    score. Empty recall -> 0.0."""
    if not items:
        return 0.0
    grounded = sum(1 for it in items if _is_grounded(it))
    return round(grounded / len(items), 3)


def _resolve(driver, query):
    with driver.session() as session:
        rec = session.run(_RESOLVE, id=query).single()
    if rec is None:
        return None
    node = rec["n"]
    data = dict(node)
    data["labels"] = list(rec["labels"])
    return data


def _neighbors(driver, ids, rels, limit):
    # Traversal whitelist: only the deterministic structural ontology is ever
    # walked. SUMMARIZES (inferred) is not traversable — even if a caller passes
    # it in ``relations`` — so a Summary can never leak into the items channel;
    # it surfaces solely through the separate summaries channel.
    #
    # ``limit`` is a server-side row bound (Cypher ``LIMIT``, after the ORDER BY)
    # so a high-degree node never streams its whole neighbour set to the client.
    rels = [r for r in rels if r in DEFAULT_RELATIONS]
    with driver.session() as session:
        rows = session.run(_NEIGHBORS, ids=list(ids), rels=rels, lim=limit)
        return [r.data() for r in rows]


def _stale(code_bound_at, target_committed_at) -> bool:
    """#4 detect-only freshness flag. A summary is stale when the target code node
    has been re-committed since the summary was bound: its current ``committed_at``
    differs from the summary's ``code_bound_at`` (equal at load by construction —
    see kg/summary.py). When either is missing, freshness is undeterminable, so we
    do NOT claim staleness (stale=False). Pure comparison — no LLM, no regeneration."""
    if code_bound_at is None or target_committed_at is None:
        return False
    return target_committed_at != code_bound_at


def _summary_channel(rows) -> list:
    """Group flat (summary, grounded-ref) rows into the separate summaries
    channel: one entry per Summary, each with its grounding refs (author-omitted,
    via :func:`_sources`), the bound commit (``code_bound_at``), the inferred
    ``edge_kind`` marker and the ``stale`` freshness flag. The summary text stays
    here — never merged into items."""
    by_id: dict = {}
    for row in rows:
        entry = by_id.get(row["id"])
        if entry is None:
            entry = {
                "id": row["id"],
                "target_id": row["target_id"],
                "claims": [json.loads(c) for c in (row["claims"] or [])],
                "edge_kind": row["edge_kind"],      # inferred marker, from the edge
                "code_bound_at": row["code_bound_at"],  # bound commit (freshness)
                # External judge's verdict (annotate-only flag), parsed from its
                # stored JSON; absent -> None (treated as unverified). palimpsest
                # never judges — it only surfaces what an external judge ingested.
                "semantic_verdict": (
                    json.loads(row["semantic_verdict"])
                    if row.get("semantic_verdict")
                    else None
                ),
                # Coverage verdict (code->claim completeness) — a separate,
                # independent annotate-only axis, parsed like semantic_verdict;
                # absent -> None. palimpsest surfaces only what a judge ingested.
                "coverage_verdict": (
                    json.loads(row["coverage_verdict"])
                    if row.get("coverage_verdict")
                    else None
                ),
                "refs": [],
                # Filled once the target's own grounded row is seen (below); the
                # target ref is always present (kg/summary.py grounds the target).
                "stale": False,
            }
            by_id[row["id"]] = entry
        entry["refs"].append({"id": row["ref_id"], **_sources(row)})
        # The freshness bound follows the TARGET node's current committed_at.
        if row["ref_id"] == entry["target_id"]:
            entry["stale"] = _stale(entry["code_bound_at"], row["committed_at"])
    return list(by_id.values())


def _summaries(driver, items, limit) -> list:
    """The inferred-summary channel for the recalled ``items`` (already bounded).

    ``limit`` is a server-side row bound (Cypher ``LIMIT``, after the ORDER BY) so
    a node with many summaries never streams the whole summary set to the client."""
    if not items:
        return []
    ids = [it["id"] for it in items]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_SUMMARIES, ids=ids, lim=limit)]
    return _summary_channel(rows)


# The inferred design-risk channels (slice 2 "위험 표시"), each a SEPARATE channel
# mirroring 'summaries'. For the recalled node ids (already depth/limit bounded),
# pull each Risk that FLAGS one of them / DesignDecision that DECIDES one of them,
# plus the code spans it grounds to. Bounded by construction. One row per
# (entity, grounded ref); ``edge_kind`` rides in as the inferred marker. RISKS /
# DECIDES are never traversable relations, so a Risk / DesignDecision can never
# leak into items — it surfaces solely through these channels.
_RISKS_CHANNEL = """
UNWIND $ids AS anchor_id
MATCH (r:Risk)-[:RISKS]->({id: anchor_id})
WITH DISTINCT r
MATCH (r)-[e:RISKS]->(g)
RETURN r.id AS id, r.title AS title, r.flags AS anchors,
       r.code_bound_at AS code_bound_at, r.semantic_verdict AS semantic_verdict,
       r.confidence AS confidence, e.edge_kind AS edge_kind,
       g.id AS ref_id, g.source_commit AS source_commit, g.path AS path,
       g.start_line AS start_line, g.end_line AS end_line,
       g.committed_at AS committed_at
ORDER BY id, ref_id
LIMIT $lim
"""

_DECISIONS_CHANNEL = """
UNWIND $ids AS anchor_id
MATCH (d:DesignDecision)-[:DECIDES]->({id: anchor_id})
WITH DISTINCT d
MATCH (d)-[e:DECIDES]->(g)
RETURN d.id AS id, d.title AS title, d.decides AS anchors,
       d.code_bound_at AS code_bound_at, d.semantic_verdict AS semantic_verdict,
       d.confidence AS confidence, e.edge_kind AS edge_kind,
       d.valid_from AS valid_from, d.valid_to AS valid_to,
       g.id AS ref_id, g.source_commit AS source_commit, g.path AS path,
       g.start_line AS start_line, g.end_line AS end_line,
       g.committed_at AS committed_at
ORDER BY id, ref_id
LIMIT $lim
"""


def _bound_anchor(anchors) -> str | None:
    """The code target whose ``committed_at`` a Risk/Decision's ``code_bound_at``
    was bound to at load — the freshness anchor. Mirrors the loaders: Risk binds
    to ``sorted(flags)[0]``; a decision binds to its first *code* DECIDES target
    (``decision:``-namespaced targets are other decisions, not code, so skipped).
    ``anchors`` arrives already sorted (the loaders sort before storing)."""
    for a in (anchors or []):
        if not a.startswith(_DECISION_NS):
            return a
    return None


def _entity_channel(rows) -> list:
    """Group flat (entity, grounded-ref) rows into an inferred design-risk channel:
    one entry per Risk/DesignDecision, each with its grounding refs (author-omitted),
    the inferred ``edge_kind`` marker, the bound commit (``code_bound_at``), the
    external judge's ``semantic_verdict`` (parsed), and the ``stale`` freshness flag
    (set from the freshness-anchor ref, mirroring the summaries channel). The title
    stays here — never merged into items."""
    by_id: dict = {}
    for row in rows:
        entry = by_id.get(row["id"])
        if entry is None:
            entry = {
                "id": row["id"],
                "title": row["title"],
                "edge_kind": row["edge_kind"],        # inferred marker, from the edge
                "code_bound_at": row["code_bound_at"],  # bound commit (freshness)
                "confidence": row["confidence"],
                # External judge's verdict (annotate-only), parsed from stored JSON;
                # absent -> None. palimpsest never judges — it only surfaces this.
                "semantic_verdict": (
                    json.loads(row["semantic_verdict"])
                    if row.get("semantic_verdict")
                    else None
                ),
                "refs": [],
                "stale": False,
                "_anchor": _bound_anchor(row.get("anchors")),
            }
            # Decision-lineage freshness (2nd axis) — decisions channel only (the
            # query returns valid_to; the risks query does not). The entry is still
            # SURFACED when superseded (전이력 보존); ``live`` is the current-currency
            # judgment, derived as valid_to IS NULL.
            if "valid_to" in row:
                entry["valid_from"] = row.get("valid_from")
                entry["valid_to"] = row.get("valid_to")
                entry["live"] = row.get("valid_to") is None
            by_id[row["id"]] = entry
        entry["refs"].append({"id": row["ref_id"], **_sources(row)})
        # Freshness follows the bound anchor's current committed_at (see loaders).
        if row["ref_id"] == entry["_anchor"]:
            entry["stale"] = _stale(entry["code_bound_at"], row["committed_at"])
    out = list(by_id.values())
    for entry in out:
        entry.pop("_anchor", None)
    return out


def _risks(driver, items, limit) -> list:
    """The inferred Risk channel for the recalled ``items`` (already bounded).

    ``limit`` is a server-side row bound (Cypher ``LIMIT`` after ORDER BY)."""
    if not items:
        return []
    ids = [it["id"] for it in items]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_RISKS_CHANNEL, ids=ids, lim=limit)]
    return _entity_channel(rows)


def _decisions(driver, items, limit) -> list:
    """The inferred DesignDecision channel for the recalled ``items`` (bounded)."""
    if not items:
        return []
    ids = [it["id"] for it in items]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_DECISIONS_CHANNEL, ids=ids, lim=limit)]
    return _entity_channel(rows)


# The inferred-RELATION channel — plain inferred EDGES (CAUSALLY_RELATES /
# RELATES_TO / CONFLICTS_WITH) touching the recalled nodes. One entry per edge
# (``WITH DISTINCT e`` dedupes when both endpoints are recalled). These rel types
# are deliberately absent from DEFAULT_RELATIONS, so they never enter items
# traversal; they surface solely here. ``$rel_types`` is the closed inferred set.
_RELATIONS_CHANNEL = """
UNWIND $ids AS anchor_id
MATCH (a {id: anchor_id})-[e]-()
WHERE type(e) IN $rel_types
WITH DISTINCT e
RETURN type(e) AS rel_type,
       startNode(e).id AS source_id, endNode(e).id AS target_id,
       e.edge_kind AS edge_kind, e.source_commit AS source_commit,
       e.created_at AS created_at, e.code_bound_at AS code_bound_at,
       e.confidence AS confidence, e.semantic_verdict AS semantic_verdict
ORDER BY rel_type, source_id, target_id
LIMIT $lim
"""


def _relation_entry(row) -> dict:
    return {
        "rel_type": row["rel_type"],
        "source_id": row["source_id"],
        "target_id": row["target_id"],
        "edge_kind": row["edge_kind"],           # inferred marker, from the edge
        "code_bound_at": row["code_bound_at"],
        "confidence": row["confidence"],
        # External judge's verdict (annotate-only), parsed from stored JSON; absent
        # -> None. palimpsest never judges — it only surfaces what was ingested.
        "semantic_verdict": (
            json.loads(row["semantic_verdict"]) if row.get("semantic_verdict") else None
        ),
        "source_commit": row["source_commit"],
        "created_at": row["created_at"],
    }


def _relations(driver, items, limit) -> list:
    """The inferred-relation channel for the recalled ``items`` (already bounded).

    ``limit`` is a server-side row bound (Cypher ``LIMIT`` after ORDER BY)."""
    if not items:
        return []
    ids = [it["id"] for it in items]
    with driver.session() as session:
        rows = [
            r.data()
            for r in session.run(
                _RELATIONS_CHANNEL,
                ids=ids,
                rel_types=list(INFERRED_RELATION_TYPES),
                lim=limit,
            )
        ]
    return [_relation_entry(row) for row in rows]


def _seed_relation_gaps(driver, query, relations):
    """For explicitly requested relations only: any that have no edge on the
    seed is an honest gap (a confident empty answer would be dishonest)."""
    with driver.session() as session:
        present = {
            r["relation"]
            for r in session.run(_SEED_REL_TYPES, id=query, rels=list(relations))
        }
    return [
        f"requested relation {rel} has no edges on seed '{query}'"
        for rel in relations
        if rel not in present
    ]


def _hop(driver, frontier, visited, relations, depth, budget):
    """One undirected BFS hop. Emits up to ``budget`` new items (deterministic
    order). Returns (items, emitted_ids, truncated)."""
    items, emitted = [], []
    truncated = False
    # Server-side bound: after skipping the (at most ``len(visited)``) already-seen
    # rows, ``budget`` unvisited must survive, plus one more to detect truncation —
    # so the first ``budget + |visited| + 1`` ordered rows are provably sufficient
    # and yield the SAME items/emitted/truncated as reading the whole set.
    read_limit = budget + len(visited) + 1
    for rec in _neighbors(driver, frontier, relations, read_limit):
        nid = rec["id"]
        if nid in visited:
            continue
        if len(items) >= budget:
            truncated = True
            break
        visited.add(nid)
        items.append(_item(rec, rec["relation"], depth))
        emitted.append(nid)
    return items, emitted, truncated


def _result(items, gaps, handle, summaries, risks=None, decisions=None, relations=None):
    return {
        "items": items,
        # Separate grounding channel (id-keyed), mirrors items — never merged.
        "sources": [{"id": it["id"], **it["sources"]} for it in items],
        # Separate inferred-summary channel — never merged into items.
        "summaries": summaries,
        # Separate inferred design-risk channels (slice 2 "위험 표시") — the Risks
        # flagging / DesignDecisions deciding the recalled code, never merged into
        # items. Empty for entry points that do not run the detection flow.
        "risks": risks or [],
        "decisions": decisions or [],
        # Separate inferred-relation channel — CAUSALLY_RELATES / RELATES_TO /
        # CONFLICTS_WITH edges touching the recalled nodes, never merged into items.
        "relations": relations or [],
        "gaps": gaps,
        "confidence": _confidence(items),
        "expand_handle": handle,
    }


def _handle(frontier, visited, next_depth, relations):
    """A pull-handle for the next hop, or None when the frontier is empty."""
    if not frontier:
        return None
    return {
        "frontier": list(frontier),
        "visited": list(visited),
        "depth": next_depth,
        "relations": list(relations),
    }


def recall(driver, query, depth=1, limit=25, relations=None):
    """Progressive, grounded, combinatorial recall from a seed node.

    ``query`` is a node id (symbol ``qualified_name`` or repo-relative path).
    Traverses CALLS / DEPENDS_ON / CONTAINS / IMPORTS up to ``depth`` hops,
    returning at most ``limit`` items. Returns
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` — the
    ``summaries`` channel holds any inferred summaries grounded in the recalled
    nodes, kept separate from ``items``.
    """
    # A gap is only raised per-relation when the caller *explicitly* narrows the
    # relation set; the default (all four) reports gaps only for an isolated or
    # unresolved seed.
    explicit_relations = relations is not None
    relations = tuple(relations) if relations is not None else DEFAULT_RELATIONS

    seed = _resolve(driver, query)
    if seed is None:
        gap = f"seed '{query}' did not resolve to any node in the graph"
        return _result([], [gap], None, [])

    seed_item = _item(seed, None, 0)
    items = [seed_item]
    visited = {seed["id"]}
    frontier = [seed["id"]]
    cur_depth = 0
    truncated = False

    while frontier and cur_depth < depth and len(items) < limit:
        cur_depth += 1
        new_items, emitted, truncated = _hop(
            driver, frontier, visited, relations, cur_depth, limit - len(items)
        )
        items.extend(new_items)
        if truncated:
            # Budget spent mid-level: resume from the level we were expanding so
            # the skipped siblings (unvisited neighbours of ``frontier``) are the
            # next hop pulled.
            break
        frontier = emitted

    # More to pull? Either the limit cut a level short, or the depth bound was
    # reached with an outer frontier that still has unvisited neighbours.
    if truncated:
        handle = _handle(frontier, visited, cur_depth, relations)
    elif frontier and _neighbors_beyond(driver, frontier, visited, relations):
        handle = _handle(frontier, visited, cur_depth + 1, relations)
    else:
        handle = None

    gaps = _seed_relation_gaps(driver, query, relations) if explicit_relations else []
    return _result(
        items, gaps, handle,
        _summaries(driver, items, limit),
        _risks(driver, items, limit),
        _decisions(driver, items, limit),
        _relations(driver, items, limit),
    )


def _neighbors_beyond(driver, frontier, visited, relations) -> bool:
    """True if the frontier has at least one still-unvisited neighbour.

    An existence check only: the first unvisited row lies within the first
    ``|visited| + 1`` ordered rows, so that server-side bound suffices — no need
    to stream the whole neighbour set."""
    for rec in _neighbors(driver, frontier, relations, len(visited) + 1):
        if rec["id"] not in visited:
            return True
    return False


# Community members, as a SEPARATE entry point (never a traversable relation:
# MEMBER_OF is deliberately absent from DEFAULT_RELATIONS, so a Community can never
# leak into ordinary items traversal — mirrors the summaries channel's separation).
# One row per member Class, grounded (commit + file:line), deterministic order.
_COMMUNITY_MEMBERS = """
MATCH (c:Class)-[:MEMBER_OF]->(:Community {id: $id})
RETURN c.id AS id, labels(c) AS labels, c.name AS name,
       c.qualified_name AS qualified_name,
       c.path AS path, c.start_line AS start_line, c.end_line AS end_line,
       c.source_commit AS source_commit, c.committed_at AS committed_at
ORDER BY id
LIMIT $lim
"""


def recall_community(driver, community_id, limit=25):
    """Recall the member Classes of a Community, as a SEPARATE entry point.

    ``community_id`` is a Community node id. Returns the same
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` shape — the
    member Classes are the grounded ``items`` (commit + file:line via
    :func:`_sources`, author-omitted), BOUNDED by ``limit``. This is
    combinatorial only (a single MEMBER_OF query + dict building) — no LLM, and
    it never walks MEMBER_OF as an ordinary traversal relation.
    """
    with driver.session() as session:
        if session.run(_RESOLVE, id=community_id).single() is None:
            gap = f"community '{community_id}' did not resolve to any node in the graph"
            return _result([], [gap], None, [])
        rows = [r.data() for r in session.run(_COMMUNITY_MEMBERS, id=community_id, lim=limit)]
    items = [_item(rec, MEMBER_OF, 1) for rec in rows]
    # 구조적 결합(community) 위에 inferred 표시: member classes' attached Summaries
    # (incl. the community's own CommunityReport, which grounds in member Classes),
    # Risks and DesignDecisions surface in their separate channels — same reverse
    # lookup as main recall, keyed off the member ids. Never merged into items.
    return _result(
        items, [], None,
        _summaries(driver, items, limit),
        _risks(driver, items, limit),
        _decisions(driver, items, limit),
        _relations(driver, items, limit),
    )


# The inferred entities (Risk / DesignDecision), as SEPARATE entry points — the
# same separation MEMBER_OF and SUMMARIZES get: their inferred edges (RISKS /
# DECIDES / SUPERSEDES / ADDRESSES_RISK) are deliberately absent from
# DEFAULT_RELATIONS, so an inferred entity can never leak into ordinary items
# traversal; it is reachable only through its own dedicated recall below.

# One row per code node a Risk flags (RISKS target), grounded, deterministic order.
_RISK_FLAGS = """
MATCH (:Risk {id: $id})-[:RISKS]->(t)
RETURN t.id AS id, labels(t) AS labels, t.name AS name,
       t.qualified_name AS qualified_name,
       t.path AS path, t.start_line AS start_line, t.end_line AS end_line,
       t.source_commit AS source_commit, t.committed_at AS committed_at
ORDER BY id
LIMIT $lim
"""

_RISK_EXISTS = "MATCH (r:Risk {id: $id}) RETURN r LIMIT 1"


def recall_risk(driver, risk_id, limit=25):
    """Recall the code a Risk flags, as a SEPARATE entry point.

    ``risk_id`` is a ``Risk`` node id (``risk:<hash>``). Returns the same
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` shape — the
    flagged code nodes are the grounded ``items`` (reached via the inferred
    ``RISKS`` edge, commit + file:line via :func:`_sources`, author-omitted),
    BOUNDED by ``limit``. Combinatorial only (a single RISKS query + dict
    building) — no LLM, and RISKS is never walked as an ordinary traversal
    relation. An unresolved id is an explicit gap, not a confident empty answer.
    """
    with driver.session() as session:
        if session.run(_RISK_EXISTS, id=risk_id).single() is None:
            gap = f"risk '{risk_id}' did not resolve to any Risk node in the graph"
            return _result([], [gap], None, [])
        rows = [r.data() for r in session.run(_RISK_FLAGS, id=risk_id, lim=limit)]
    items = [_item(rec, "RISKS", 1) for rec in rows]
    return _result(items, [], None, [])


# One row per DesignDecision target, EACH carrying its own edge type as ``relation``
# (DECIDES code|decision / SUPERSEDES decision / ADDRESSES_RISK risk). The three rel
# types are a closed whitelist baked into the query — a decision's outgoing edges are
# all inferred by construction; naming them keeps recall to the intended edges only.
# Deterministic order (id, relation).
_DECISION_TARGETS = """
MATCH (:DesignDecision {id: $id})-[e:DECIDES|SUPERSEDES|ADDRESSES_RISK]->(t)
RETURN t.id AS id, type(e) AS relation, labels(t) AS labels, t.name AS name,
       t.qualified_name AS qualified_name,
       t.path AS path, t.start_line AS start_line, t.end_line AS end_line,
       t.source_commit AS source_commit, t.committed_at AS committed_at
ORDER BY id, relation
LIMIT $lim
"""

_DECISION_EXISTS = "MATCH (d:DesignDecision {id: $id}) RETURN d LIMIT 1"


def recall_decision(driver, decision_id, limit=25):
    """Recall what a DesignDecision commits to, as a SEPARATE entry point.

    ``decision_id`` is a ``DesignDecision`` node id (``decision:<hash>``). Returns
    the same ``{items, sources, summaries, gaps, confidence, expand_handle}`` shape
    — the decision's targets are the ``items``, each labelled by its own edge type
    (``relation`` = DECIDES / SUPERSEDES / ADDRESSES_RISK), BOUNDED by ``limit``.
    A DECIDES *code* target is grounded (commit + file:line via :func:`_sources`);
    SUPERSEDES / ADDRESSES_RISK targets are inferred entities (no code span), so
    ``confidence`` (grounding coverage) reflects the mix honestly. Combinatorial
    only (a single query + dict building) — no LLM, and these inferred edges are
    never walked as ordinary traversal relations. An unresolved id is an explicit
    gap, not a confident empty answer.
    """
    with driver.session() as session:
        if session.run(_DECISION_EXISTS, id=decision_id).single() is None:
            gap = f"decision '{decision_id}' did not resolve to any DesignDecision node in the graph"
            return _result([], [gap], None, [])
        rows = [r.data() for r in session.run(_DECISION_TARGETS, id=decision_id, lim=limit)]
    items = [_item(rec, rec["relation"], 1) for rec in rows]
    return _result(items, [], None, [])


def expand(driver, handle, limit=25):
    """Pull the next hop from an ``expand_handle`` returned by :func:`recall`.

    Combinatorial, on-demand continuation: one more BFS hop from the handle's
    frontier, skipping already-seen nodes, bounded by ``limit``. Returns the
    same ``{items, sources, summaries, gaps, confidence, expand_handle}`` shape.
    """
    if not handle or not handle.get("frontier"):
        return _result([], ["no frontier to expand"], None, [])

    relations = tuple(handle.get("relations") or DEFAULT_RELATIONS)
    visited = set(handle["visited"])
    frontier = list(handle["frontier"])
    depth = handle["depth"]

    new_items, emitted, truncated = _hop(
        driver, frontier, visited, relations, depth, limit
    )

    if truncated:
        next_handle = _handle(frontier, visited, depth, relations)
    elif emitted and _neighbors_beyond(driver, emitted, visited, relations):
        next_handle = _handle(emitted, visited, depth + 1, relations)
    else:
        next_handle = None

    return _result(
        new_items, [], next_handle,
        _summaries(driver, new_items, limit),
        _risks(driver, new_items, limit),
        _decisions(driver, new_items, limit),
        _relations(driver, new_items, limit),
    )


# The branch-scoped peers of one symbol: every node whose ``qualified_name`` equals
# the symbol AND whose ``branch`` is in the caller's set. Grouping is by the stored
# ``qualified_name`` property (branch-scoped ids differ, so ids cannot group them).
# ``branch IN $branches`` excludes the bare-id plane (branch=null) and any
# unspecified branch by construction (ac-6). Author (%ae) is NEVER selected —
# author-omission holds by projection. Deterministic neutral order (branch) for a
# stable tiebreak; the real order key is computed client-side (the UTC instant).
_BRANCH_PEERS = """
MATCH (n) WHERE n.qualified_name = $symbol AND n.branch IN $branches
RETURN n.id AS id, n.branch AS branch, n.qualified_name AS qualified_name,
       labels(n) AS labels, n.name AS name,
       n.path AS path, n.start_line AS start_line, n.end_line AS end_line,
       n.source_commit AS source_commit, n.committed_at AS committed_at
ORDER BY n.branch
"""


def _utc_instant(committed_at):
    """The absolute UTC instant of a raw ``committed_at`` (%cI, ISO-8601 with tz
    offset). py3.12 ``fromisoformat`` parses the offset -> a tz-aware datetime that
    compares by absolute instant. Missing/unparseable -> None (sorts last, never
    freshest). A tz-NAIVE value (no offset) is also treated as unparseable: it
    cannot be compared against the tz-aware peers without raising, and the %cI
    contract always emits an offset, so a naive value is off-contract, not a
    rankable instant. Pure comparison — no LLM, no mutation of the stored value."""
    if not committed_at:
        return None
    try:
        instant = datetime.fromisoformat(committed_at)
    except (ValueError, TypeError):
        return None
    if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:
        return None  # tz-naive: off-contract, sort last like unparseable
    return instant


def _peer_semantic(driver, peer_id, limit):
    """The inferred semantic layer bound to ONE peer, read exactly like the main
    recall channels (each helper json.loads the stored verdict; absent -> None).
    DISPLAY-ONLY: freshness orders the peers, this is shown alongside, non-merged.
    palimpsest generates none of it — it only surfaces what an external judge loaded."""
    items = [{"id": peer_id}]
    return {
        "summaries": _summaries(driver, items, limit),
        "risks": _risks(driver, items, limit),
        "decisions": _decisions(driver, items, limit),
        "relations": _relations(driver, items, limit),
    }


def _ranked_peers(rows):
    """Order the peers newest-UTC-instant first, with a stability-only neutral
    tiebreak (branch name) — branch name carries NO priority. Peers with an
    unparseable/missing instant sort last. ``freshest:true`` on every peer at the
    max instant (co-freshest when tied; no fabricated single winner)."""
    parseable, unparseable = [], []
    for r in rows:
        r["_instant"] = _utc_instant(r.get("committed_at"))
        (parseable if r["_instant"] is not None else unparseable).append(r)
    # Stable two-key sort: tiebreak (branch asc) first, then primary (instant desc).
    parseable.sort(key=lambda r: r["branch"])
    parseable.sort(key=lambda r: r["_instant"], reverse=True)
    unparseable.sort(key=lambda r: r["branch"])
    ordered = parseable + unparseable
    top = parseable[0]["_instant"] if parseable else None
    for r in ordered:
        # aware-datetime == compares the absolute instant, so cross-zone ties match.
        r["freshest"] = top is not None and r["_instant"] == top
    return ordered


def _peer_entry(driver, r, limit):
    return {
        "id": r["id"],
        "branch": r["branch"],
        "qualified_name": r["qualified_name"],
        "kind": _kind(r.get("labels")),
        "committed_at": r.get("committed_at"),
        "freshest": r["freshest"],
        # per-branch grounding (author-omitted via _sources) — the 출처.
        "source_commit": r.get("source_commit"),
        "sources": _sources(r),
        # display-only semantic annotation (external-bound; verdict + confidence).
        "semantic": _peer_semantic(driver, r["id"], limit),
    }


# Similarity floor for vector-KNN recall. Neo4j's cosine index returns a
# NORMALIZED score = (1 + cosine) / 2 for cosine >= 0, and CLAMPS negative cosines
# to 0.5 (orthogonal and opposite both score 0.5) — verified empirically. A hit
# below this floor is not a confident match, so it is reported as an explicit gap
# rather than filling k with a low-similarity answer (ac-3 honesty). 0.6 ~ cosine 0.2.
_MIN_COSINE_SCORE = 0.6


class InvalidQueryVector(ValueError):
    """A query_vector that is not a valid EMBEDDING_DIM cosine vector (wrong
    length, or containing NaN/inf). Raised BEFORE the driver query so the caller
    gets a typed rejection instead of a raw Neo4j exception (ac-5: the query
    vector is caller-supplied — palimpsest never embeds)."""


def _validate_query_vector(query_vector) -> None:
    """Reject a malformed query vector up front: it must have exactly
    EMBEDDING_DIM components (the index dimension) and no NaN/inf."""
    try:
        n = len(query_vector)
    except TypeError as exc:
        raise InvalidQueryVector("query_vector must be a sequence of floats") from exc
    if n != EMBEDDING_DIM:
        raise InvalidQueryVector(
            f"query_vector must have length {EMBEDDING_DIM}, got {n}"
        )
    if not all(math.isfinite(x) for x in query_vector):
        raise InvalidQueryVector("query_vector must not contain NaN or inf")


# Top-k cosine over the Summary VECTOR INDEX, then (like the summaries channel)
# one row per (summary, grounded-ref) so `_summary_channel` can group + set stale
# off the TARGET ref. ``score`` (the index's cosine similarity) and the target's
# ``branch`` ride along per row. Branch scoping mirrors reconcile's ``branch IN
# $branches`` filter (null $branches = all planes); ORDER BY score DESC keeps the
# grouping — and thus the summaries channel — in cosine-descending order.
_SEMANTIC_KNN = """
CALL db.index.vector.queryNodes($index_name, $k, $query_vector) YIELD node AS s, score
MATCH (s)-[:SUMMARIZES]->(tgt {id: s.target_id})
WHERE $branches IS NULL OR tgt.branch IN $branches
MATCH (s)-[r:SUMMARIZES]->(g)
RETURN s.id AS id, s.target_id AS target_id, s.claims AS claims,
       s.code_bound_at AS code_bound_at, s.semantic_verdict AS semantic_verdict,
       s.coverage_verdict AS coverage_verdict,
       r.edge_kind AS edge_kind, score AS score, tgt.branch AS branch,
       g.id AS ref_id, g.source_commit AS source_commit, g.path AS path,
       g.start_line AS start_line, g.end_line AS end_line,
       g.committed_at AS committed_at
ORDER BY score DESC, id, ref_id
"""


def recall_semantic(driver, query_vector, branches=None, limit=25):
    """Standalone vector-KNN recall: the top-k cosine Summaries for a query vector.

    ``query_vector`` is a caller-supplied EMBEDDING_DIM float vector (palimpsest
    never embeds — ac-5 provider-free). Runs a top-k cosine query over the Summary
    VECTOR INDEX and returns the SAME bounded ``{items, sources, summaries, ...}``
    shape as the sibling entry points; the matched Summaries surface in the
    separate ``summaries`` channel, cosine-descending, each carrying:

    * ``score`` — the index's cosine similarity, kept SEPARATE from the result's
      grounding-coverage ``confidence`` (a cosine is never a confidence, ac-3);
    * ``stale`` — the freshness flag every Summary-surfacing path attaches (set via
      :func:`_summary_channel` off the SUMMARIZES target's current committed_at); and
    * ``branch`` — the plane the hit came from (ADR-20260703 branch-scoped identity).

    ``branches`` (default None = all planes) scopes the KNN candidate set to those
    branch planes so a global cosine query cannot silently mix branch-scoped planes.
    ``limit`` bounds k (clamped to >=1, since ``queryNodes`` requires k>=1). A best
    match below the similarity floor is an explicit ``gap`` — never a confident-empty
    or low-similarity filled-k answer (ac-3 honesty). Combinatorial only: a single
    vector query + dict building, no LLM anywhere.
    """
    _validate_query_vector(query_vector)
    k = max(1, limit)  # queryNodes requires k>=1 (unlike a Cypher LIMIT 0)
    branch_filter = sorted(set(branches)) if branches is not None else None

    with driver.session() as session:
        rows = [
            r.data()
            for r in session.run(
                _SEMANTIC_KNN,
                index_name=VECTOR_INDEX_NAME,
                k=k,
                query_vector=list(query_vector),
                branches=branch_filter,
            )
        ]

    # Similarity floor: drop hits below it (all rows of one summary share its
    # score) so k is never filled with a low-similarity, confident-empty answer.
    kept = [row for row in rows if row["score"] >= _MIN_COSINE_SCORE]
    # Per-summary (score, branch), first row wins under the ORDER BY score DESC.
    meta = {}
    for row in kept:
        meta.setdefault(row["id"], (row["score"], row["branch"]))

    entries = _summary_channel(kept)  # groups + sets stale off the target ref
    for entry in entries:
        score, branch = meta[entry["id"]]
        entry["score"] = score    # cosine similarity — SEPARATE from confidence
        entry["branch"] = branch  # branch plane of the hit (ADR-20260703)
    entries.sort(key=lambda e: e["score"], reverse=True)

    gaps = []
    if not entries:
        gaps = [
            f"no Summary within similarity floor {_MIN_COSINE_SCORE} of the query "
            f"vector (branches={branch_filter})"
        ]
    return _result([], gaps, None, entries)


def reconcile_recall(driver, symbol, branches, limit=25):
    """N-way peer reconcile recall over one symbol's branch-scoped planes.

    Compares the branch-scoped peers of ``symbol`` across EXACTLY the caller's
    ``branches`` (ac-6) — as equals, with NO privileged branch. Peers are ranked
    by the absolute UTC instant of their ``committed_at`` (newest first; a neutral
    branch-name tiebreak is stability-only), the max-instant peer(s) flagged
    ``freshest``. Each peer carries its per-branch grounding (author-omitted) and
    a DISPLAY-ONLY semantic annotation read from the already-stored inferred layer
    (verdict + confidence + source_commit + code_bound_at) — palimpsest generates
    nothing here (zero LLM/provider calls).

    Cross-branch conflict is surfaced on two non-generative tracks, labeled
    distinctly: ``conflict_edges`` = EXISTING CONFLICTS_WITH edges touching the
    peers (via the relations channel, never newly created); ``code_divergence`` =
    a pure computed observation that the peers share the symbol but differ in
    ``source_commit``. Returns
    ``{symbol, branches, peers, code_divergence, conflict_edges, gaps}``.
    """
    branch_set = sorted(set(branches))
    with driver.session() as session:
        rows = [
            r.data()
            for r in session.run(_BRANCH_PEERS, symbol=symbol, branches=branch_set)
        ]

    if not rows:
        return {
            "symbol": symbol,
            "branches": branch_set,
            "peers": [],
            "code_divergence": {"source_commits": [], "diverged": False},
            "conflict_edges": [],
            "gaps": [f"symbol '{symbol}' has no peers in branches {branch_set}"],
        }

    ordered = _ranked_peers(rows)
    peers = [_peer_entry(driver, r, limit) for r in ordered]

    # Track (b): structural divergence — a pure computed observation (no edge
    # written, no edge_kind='inferred' laundering). Peers share the symbol; if they
    # differ in source_commit, the code has diverged across branches.
    source_commits = sorted({r.get("source_commit") for r in rows if r.get("source_commit")})
    code_divergence = {
        "source_commits": source_commits,
        "diverged": len(source_commits) > 1,
    }

    # Track (a): EXISTING CONFLICTS_WITH edges touching the peers, surfaced via the
    # relations channel (never newly created). Distinct from the computed divergence.
    peer_items = [{"id": r["id"]} for r in ordered]
    conflict_edges = [
        e for e in _relations(driver, peer_items, limit)
        if e["rel_type"] == "CONFLICTS_WITH"
    ]

    return {
        "symbol": symbol,
        "branches": branch_set,
        "peers": peers,
        "code_divergence": code_divergence,
        "conflict_edges": conflict_edges,
        "gaps": [],
    }


# ── churn / co-change: the MODIFIES (Episode -> File) recall channels ─────────
# MODIFIES is deterministic but deliberately ABSENT from DEFAULT_RELATIONS, so an
# author-bearing Episode is never dragged into ordinary items traversal. These two
# SEPARATE entry points surface it safely: they NEVER project the Episode (no
# ``RETURN e`` / ``e.author``) — only the File endpoints, via :func:`_sources`
# (author omitted), exactly like the summaries / community channels. Ranking is a
# pure count DESC + total-order (id) tiebreak, so a new / sparse repo degrades
# gracefully (fewer hotspots) rather than needing a hardcoded count threshold.

# Hotspot Files, ranked by how many DISTINCT commits (Episodes) touched them. ``e``
# is used ONLY inside ``count(DISTINCT e)`` — never returned, so no author leaks.
_CHURN = """
MATCH (f:File)<-[:MODIFIES]-(e:Episode)
WITH f, count(DISTINCT e) AS churn
RETURN f.id AS id, labels(f) AS labels, f.name AS name,
       f.qualified_name AS qualified_name,
       f.path AS path, f.start_line AS start_line, f.end_line AS end_line,
       f.source_commit AS source_commit, f.committed_at AS committed_at,
       churn AS churn
ORDER BY churn DESC, id
LIMIT $lim
"""

# Bound on per-Episode fan-out into co-changed files: a mega-commit touching
# thousands of files must not blow up the co-change join. The caller's ``limit``
# bounds the RETURNED rows; this named cap bounds the INTERMEDIATE expansion each
# Episode contributes (a threshold that must exist gets a named module constant,
# never a magic literal buried in the query).
_COCHANGE_FANOUT_CAP = 512

# Files co-changed with the seed File: another File touched by the SAME Episode.
# ``f2`` is constrained to the seed's OWN branch plane (``coalesce`` handles the
# bare null plane) so a bare Episode never bridges two branch-scoped planes
# (mirrors recall_semantic's branch guard). The Episode is again never projected —
# only File2 endpoints surface. The per-Episode fan-out is capped server-side.
_COCHANGE = """
MATCH (f:File {id: $id})<-[:MODIFIES]-(e:Episode)
CALL {
    WITH e, f
    MATCH (e)-[:MODIFIES]->(f2:File)
    WHERE f2.id <> f.id
      AND coalesce(f2.branch, '') = coalesce(f.branch, '')
    RETURN f2 ORDER BY f2.id LIMIT $fanout
}
WITH f2, count(DISTINCT e) AS cochange
RETURN f2.id AS id, labels(f2) AS labels, f2.name AS name,
       f2.qualified_name AS qualified_name,
       f2.path AS path, f2.start_line AS start_line, f2.end_line AS end_line,
       f2.source_commit AS source_commit, f2.committed_at AS committed_at,
       cochange AS cochange
ORDER BY cochange DESC, id
LIMIT $lim
"""


def recall_churn(driver, limit=25):
    """Recall the churn hotspots — Files ranked by how many commits touched them.

    A SEPARATE, global entry point over the MODIFIES spine. Returns the standard
    ``{items, sources, summaries, ...}`` shape; each item is a hotspot File
    (grounded, author-omitted) carrying a ``churn`` count, ordered count DESC with
    an id tiebreak (deterministic, run-stable) and BOUNDED by ``limit``. An empty
    MODIFIES graph is an explicit gap, never a crash (graceful-empty). Combinatorial
    only (one aggregation query + dict building) — no LLM.
    """
    with driver.session() as session:
        rows = [r.data() for r in session.run(_CHURN, lim=limit)]
    items = [_item(rec, MODIFIES, 1) for rec in rows]
    for it, rec in zip(items, rows):
        it["churn"] = rec["churn"]
    gaps = [] if items else ["no MODIFIES edges in the graph — churn recall is empty"]
    return _result(items, gaps, None, [])


def recall_cochange(driver, file_id, limit=25):
    """Recall the Files that co-changed with ``file_id`` (same-commit co-change).

    A SEPARATE entry point: File2s touched by the SAME Episode as the seed File,
    ranked by co-change count DESC (id tiebreak), constrained to the seed's own
    branch plane, BOUNDED by ``limit`` and by a per-Episode fan-out cap. An
    unresolved seed / no co-change is an explicit gap, never a confident empty
    answer. The Episode is never projected (author-omitted). Combinatorial only.
    """
    with driver.session() as session:
        if session.run(_RESOLVE, id=file_id).single() is None:
            gap = f"file '{file_id}' did not resolve to any node in the graph"
            return _result([], [gap], None, [])
        rows = [
            r.data()
            for r in session.run(
                _COCHANGE, id=file_id, lim=limit, fanout=_COCHANGE_FANOUT_CAP
            )
        ]
    items = [_item(rec, MODIFIES, 1) for rec in rows]
    for it, rec in zip(items, rows):
        it["cochange"] = rec["cochange"]
    gaps = [] if items else [f"file '{file_id}' has no co-changed files"]
    return _result(items, gaps, None, [])


# ── test-impact: the backward-CALLS recall channel (#7) ───────────────────────
# "Which tests exercise this changed production Method?" A dedicated, read-only entry
# point that walks CALLS BACKWARD (seed <- caller) to the test Methods that transitively
# call the seed. Deliberately a PER-HOP bounded BFS (mirrors recall's _hop), NOT a
# variable-length [:CALLS*1..depth] path: a var-length path bounds only OUTPUT rows,
# not COMPUTE — a hot method explodes it (regressing the fix-#6 traversal bound) and it
# is not rebuild-deterministic. Here each hop's read is server-bounded, id-ordered
# before LIMIT, and per-frontier-node fan-out capped, so COMPUTE itself is bounded.

# Per-hop fan-out cap: a hot production Method may be called by a huge number of test
# Methods; this bounds the callers ONE frontier node contributes per hop so a single
# high-degree seed never explodes the backward traversal (mirrors _COCHANGE_FANOUT_CAP;
# a threshold that must exist gets a named constant, never a magic literal in the query).
_TEST_IMPACT_FANOUT_CAP = 512

# One backward-CALLS hop: ALL callers of a frontier of Methods. CALLS is walked BACKWARD
# (caller -[:CALLS]-> callee={frontier}) with NO is_test filter — production intermediaries
# MUST be traversed THROUGH: a ``test -> production_helper -> seed`` chain (the dominant
# far/indirect case) is only found if the production helper stays in the frontier for the
# next hop. So the frontier expands through EVERY caller, and the is_test partition happens
# in Python (:func:`_test_impact_hop`): test callers (is_test=true) become answers, production
# callers are stepping-stones. ``caller.is_test`` is projected so that partition is possible
# (a plain PROPERTY read — never a whole-node projection). DISTINCT dedups a caller reached
# from several frontier nodes; per-node fan-out is capped server-side; the whole result is
# id-ordered before LIMIT for rebuild-determinism. The caller Method is projected by the
# CALLER through _item/_sources (author-omitted) — this query NEVER returns a whole node, so
# the per-node author (stamped on every Method) can not leak.
_TEST_CALLERS = """
UNWIND $ids AS sid
CALL {
    WITH sid
    MATCH (callee {id: sid})<-[:CALLS]-(caller)
    RETURN caller ORDER BY caller.id LIMIT $fanout
}
RETURN DISTINCT caller.id AS id, caller.is_test AS is_test,
       labels(caller) AS labels, caller.name AS name,
       caller.qualified_name AS qualified_name,
       caller.path AS path, caller.start_line AS start_line, caller.end_line AS end_line,
       caller.source_commit AS source_commit, caller.committed_at AS committed_at
ORDER BY id
LIMIT $lim
"""

# Standing gap (ac-3 honesty), ALWAYS emitted: static CALLS is a LOWER BOUND on the
# impacted tests. It cannot see reflective / dependency-injection / polymorphic-dispatch
# test callers, so an empty or short result must NOT be read as "no tests impacted" —
# completeness is never claimed on this channel.
_STATIC_LOWER_BOUND_GAP = (
    "static CALLS is a lower bound: reflective / DI / polymorphic-dispatch test "
    "callers are invisible to it, AND at a tight limit production callers interleaved "
    "in the id-ordered frontier can crowd out statically-visible test callers "
    "(mechanical under-fill) — so an empty or short result does NOT mean 'no tests "
    "impacted'; completeness is not claimed"
)


def _test_impact_reingest_gap(seed_id) -> str:
    """The DISTINCT zero-coverage gap: when the backward traversal finds no is_test
    caller, advise a re-ingest so a graph that PREDATES the is_test marker (every node
    lacks it) is not silently read as 'this seed has no test callers'."""
    return (
        f"no is_test test caller found for '{seed_id}' via static CALLS; if unexpected, "
        "the graph may predate the is_test marker — re-ingest to populate is_test before "
        "reading this as 'no test callers'"
    )


def _test_callers(driver, ids, limit):
    """Backward-CALLS callers of a frontier — ALL of them (production intermediaries AND
    test callers; each row carries ``is_test`` so the caller partitions them). Rows only;
    projection is the caller's job via _item/_sources. ``limit`` is a server-side row bound
    (Cypher LIMIT after ORDER BY) and per-node fan-out is capped, so a hot seed never streams
    its whole caller set to the client."""
    with driver.session() as session:
        rows = session.run(
            _TEST_CALLERS, ids=list(ids), lim=limit, fanout=_TEST_IMPACT_FANOUT_CAP
        )
        return [r.data() for r in rows]


def _test_impact_hop(driver, frontier, visited, depth, budget):
    """One backward-CALLS BFS hop. The frontier expands through ALL backward callers —
    a production intermediary is a stepping-stone kept in ``emitted`` for the next hop — while
    ONLY ``is_test=true`` callers are collected as result ``items``. That split is the fix for
    the far/indirect case (``test -> production_helper -> seed``): the production helper is
    traversed THROUGH instead of pruned, so the distant test reaching the seed through it is
    found. Emits up to ``budget`` new test items (deterministic id order). Returns
    (items, emitted_ids, truncated). Mirrors :func:`_hop`: the server read is bounded to
    ``budget + |visited| + 1`` rows."""
    items, emitted = [], []
    truncated = False
    read_limit = budget + len(visited) + 1
    for rec in _test_callers(driver, frontier, read_limit):
        nid = rec["id"]
        if nid in visited:
            continue
        if len(items) >= budget:
            truncated = True
            break
        visited.add(nid)
        emitted.append(nid)             # EVERY caller advances the frontier (production + test)
        if rec.get("is_test"):          # only test callers are answers; production ones step through
            items.append(_item(rec, CALLS, depth))
    return items, emitted, truncated


def _test_impact_from_seeds(driver, seed_ids, depth, limit):
    """The shared backward-CALLS BFS, seeded with a SET of production Method ids — one for
    :func:`recall_test_impact`, the WHOLE resolved changeset for
    :func:`recall_changeset_impact`. Walks ``CALLS`` BACKWARD hop by hop (a per-hop bounded
    BFS mirroring :func:`recall`, NOT a variable-length ``[:CALLS*1..depth]`` path),
    expanding the frontier through ALL backward callers while collecting ONLY ``is_test``
    callers as items. A SHARED ``visited`` set means a test reached from several seeds — or
    via several paths — is DISTINCT-deduped to EXACTLY one item, and total items are bounded
    by ``limit``. The initial frontier is id-sorted (and every later frontier is already
    id-ordered by :func:`_test_impact_hop`) so selection under the limit is rebuild-stable.
    ``depth <= 0`` (or an empty seed set) degrades to an empty result. Returns ``items``.

    Bounding (ac-3 — COMPUTE, not just output rows): each hop reads at most
    ``budget + |visited| + 1`` server-side rows, per-frontier-node fan-out is capped at
    ``_TEST_IMPACT_FANOUT_CAP``, and total items are bounded by ``limit``.
    """
    items = []
    visited = set(seed_ids)
    frontier = sorted(visited)
    cur_depth = 0
    while frontier and cur_depth < depth and len(items) < limit:
        cur_depth += 1
        new_items, emitted, truncated = _test_impact_hop(
            driver, frontier, visited, cur_depth, limit - len(items)
        )
        items.extend(new_items)
        if truncated:
            # Item budget spent mid-level; the top-``limit`` id-ordered test callers are
            # returned (a bounded answer — this channel does not page beyond ``limit``).
            break
        frontier = emitted  # next hop expands from the callers just found
    return items


def recall_test_impact(driver, method_id, depth=10, limit=25):
    """Recall the test Methods that transitively call a production Method (#7).

    A SEPARATE, read-only entry point. ``method_id`` is the changed production Method
    node id (the seed). Walks ``CALLS`` BACKWARD (seed <- caller) hop by hop — a per-hop
    bounded BFS mirroring :func:`recall`, NOT a variable-length ``[:CALLS*1..depth]``
    path — expanding the frontier through ALL backward callers (production intermediaries
    included) while collecting ONLY ``is_test=true`` callers as ``items``. So the returned
    items are the test Methods reaching the seed directly (1 hop) or TRANSITIVELY through a
    helper chain (>1 hop) — and the intermediaries may be PRODUCTION code (the dominant
    far/indirect case: ``test -> production_helper -> seed``, which a per-hop is_test filter
    on the frontier would silently lose). DISTINCT-deduped across call paths. Same bounded
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` shape as the sibling
    channels; the test Methods are the grounded ``items`` (commit + file:line via
    :func:`_sources`, author-omitted — never a whole-node projection). Combinatorial only
    (bounded CALLS traversal + dict building), no LLM.

    Bounding (ac-3 — COMPUTE, not just output rows): each hop reads at most
    ``budget + |visited| + 1`` server-side rows, per-frontier-node fan-out is capped at
    ``_TEST_IMPACT_FANOUT_CAP``, total items are bounded by ``limit``, and callers are
    id-ordered before the LIMIT for rebuild-determinism — so a hot seed never explodes
    and the result is run-stable. ``depth`` is the transitive-hop ceiling (``limit`` is
    the real item bound); ``depth <= 0`` degrades gracefully to a seed-only (empty)
    result.

    Gaps (ac-3 honesty): the static-lower-bound note is ALWAYS present (static CALLS
    misses reflective / DI / polymorphic-dispatch callers); when NO test caller is found,
    a DISTINCT re-ingest advisory is added so a graph predating the is_test marker is not
    mistaken for 'no test callers'. Completeness is never claimed.
    """
    gaps = [_STATIC_LOWER_BOUND_GAP]

    seed = _resolve(driver, method_id)
    if seed is None:
        gaps.append(f"seed '{method_id}' did not resolve to any node in the graph")
        return _result([], gaps, None, [])

    # Seed the shared backward-CALLS BFS with the single resolved Method id.
    items = _test_impact_from_seeds(driver, [seed["id"]], depth, limit)

    if not items:
        gaps.append(_test_impact_reingest_gap(seed["id"]))
    return _result(items, gaps, None, [])


# ── changeset-impact: the changeset-level generalization of test-impact ───────
# "Given a code CHANGE (a changeset), which tests transitively cover the changed code?"
# A QUERY-SIDE derivation, NOT edge materialisation: it resolves the changeset to a SET
# of production Method seeds and reuses the SAME backward-CALLS BFS as recall_test_impact,
# seeded with the whole set at once (a shared ``visited`` dedups a test reached from several
# seeds to one item). It materialises NO new edge and calls NO LLM.

# Resolve the changed FILES of a changeset to the production Methods they CONTAIN. Files are
# keyed by branch-scoped ``id`` but carry a ``path`` property — match on ``path``. CONTAINS*
# spans File -> Class -> Method (a Method nested under its Class under its File). This is a
# FILE-GRANULARITY OVER-APPROXIMATION: MODIFIES / diff is file-level and there is no
# line->method mapping, so EVERY Method in a changed File is taken as (possibly) changed.
_CHANGESET_FILE_METHODS = (
    "MATCH (f:File) WHERE f.path IN $paths "
    "MATCH (f)-[:CONTAINS*]->(m:Method) RETURN DISTINCT m.id AS id"
)

# Resolve a changeset COMMIT to the production Methods its MODIFIED Files contain. Same
# file-granularity over-approximation. Episode {id: commit} -[:MODIFIES]-> File, then
# CONTAINS* down to each Method.
_CHANGESET_COMMIT_METHODS = (
    "MATCH (e:Episode {id: $commit})-[:MODIFIES]->(:File)-[:CONTAINS*]->(m:Method) "
    "RETURN DISTINCT m.id AS id"
)

# Standing gap (ac-3 honesty), emitted WHENEVER file / commit seeds were used: those seeds
# resolve at FILE granularity, so an impacted test may in fact cover an UNCHANGED method that
# merely shares a changed file — the impacted set is an UPPER bound for those seeds. (Explicit
# ``methods`` seeds are exact and do not trigger this note.)
_CHANGESET_OVERAPPROX_GAP = (
    "file / commit seeds over-approximate to FILE granularity: every Method in a changed "
    "File is treated as changed (MODIFIES / diff is file-level, with no line->method "
    "mapping), so an impacted test may cover an UNCHANGED method sharing a changed file — "
    "the impacted set is an upper bound for those seeds"
)


def _resolve_changeset_seeds(driver, files, methods, commit):
    """Resolve a changeset — any combination of changed Files, explicit Methods, and a
    commit — to the deduped SET of production Method seed ids. Explicit ``methods`` resolve
    EXACTLY via :func:`_resolve` (id / qualified_name), keeping only Method-labelled nodes;
    ``files`` and ``commit`` resolve at FILE granularity (every Method a changed File
    contains — see :data:`_CHANGESET_OVERAPPROX_GAP`). Combinatorial only, no LLM."""
    seed_ids = set()
    if methods:
        for m in methods:
            node = _resolve(driver, m)
            if node is not None and "Method" in (node.get("labels") or ()):
                seed_ids.add(node["id"])
    if files or commit:
        with driver.session() as session:
            if files:
                for r in session.run(_CHANGESET_FILE_METHODS, paths=list(files)):
                    seed_ids.add(r["id"])
            if commit:
                for r in session.run(_CHANGESET_COMMIT_METHODS, commit=commit):
                    seed_ids.add(r["id"])
    return seed_ids


def recall_changeset_impact(
    driver, *, files=None, methods=None, commit=None, depth=10, limit=25
):
    """Recall the test Methods impacted by a CHANGESET — the changeset-level generalization
    of :func:`recall_test_impact`.

    A SEPARATE, read-only entry point that answers "given a code change, which tests
    transitively cover the changed code (and might break)?". The changeset is any combination
    of changed ``files`` (repo-relative paths), explicit ``methods`` (Method ids /
    qualified_names), and a ``commit`` id; at least one must be given. Step 1 resolves it to a
    deduped SET of production Method seeds (``files`` / ``commit`` over-approximate to file
    granularity — every Method in a changed File; ``methods`` resolve exactly). Step 2 runs
    the SAME per-hop bounded backward-CALLS BFS as :func:`recall_test_impact`, seeded with the
    WHOLE set at once via :func:`_test_impact_from_seeds`, so a test reached from several seeds
    is DISTINCT-deduped to EXACTLY one item and total items are bounded by ``limit``.

    QUERY-SIDE only: it materialises NO new edge and calls NO LLM — a pure combinatorial
    derivation over the existing CALLS traversal. Same bounded
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` shape; the impacted test
    Methods are the grounded ``items`` (author-omitted via :func:`_item` / :func:`_sources`).

    Gaps (ac-3 honesty): the static-lower-bound note is ALWAYS present; the over-approximation
    note rides along WHENEVER ``files`` / ``commit`` seeds were used; an empty changeset, or
    one that resolves to no Method seed, is an EXPLICIT gap (never a confident empty answer);
    and when seeds resolved but no test caller was found, the DISTINCT re-ingest advisory is
    added, exactly as :func:`recall_test_impact`.
    """
    gaps = [_STATIC_LOWER_BOUND_GAP]
    if files or commit:
        gaps.append(_CHANGESET_OVERAPPROX_GAP)

    if not (files or methods or commit):
        gaps.append(
            "changeset is empty: provide at least one of files / methods / commit "
            "to resolve a Method seed set"
        )
        return _result([], gaps, None, [])

    seed_ids = _resolve_changeset_seeds(driver, files, methods, commit)
    if not seed_ids:
        gaps.append(
            "changeset did not resolve to any Method seed in the graph (no matching "
            "File / commit / Method) — an empty result here means 'nothing resolved', "
            "not 'no tests impacted'"
        )
        return _result([], gaps, None, [])

    # Seed the shared backward-CALLS BFS with the WHOLE resolved changeset at once.
    items = _test_impact_from_seeds(driver, seed_ids, depth, limit)

    if not items:
        gaps.append(_test_impact_reingest_gap(f"{len(seed_ids)} changeset seed method(s)"))
    return _result(items, gaps, None, [])


# ── runtime-coverage overlay: the OBSERVED-execution AUGMENT of static test-impact ──────
# "Which tests were OBSERVED at runtime to cover this production Method?" — reads the
# COVERS(edge_kind='runtime') edges the ``kg.coverage`` loader materialised from an external
# producer's per-test coverage (ADR-20260714). A SEPARATE, provenance-distinct channel: it
# AUGMENTS the static CALLS channel (:func:`recall_test_impact`), never replaces it. Runtime
# coverage is a DIRECT observation (a test executed a method or it did not), so this is a
# single backward-COVERS hop — no transitive BFS. Combinatorial only, no LLM, no build.

# Backward COVERS from a production Method to the test Methods observed to cover it. Rows
# only; projection is via :func:`_item` (author-omitted, never a whole-node projection). The
# edge's ``edge_kind`` is projected so the item carries the runtime-overlay provenance marker.
_RUNTIME_TEST_CALLERS = """
MATCH (prod:Method {id: $id})<-[r:COVERS]-(t:Method)
RETURN t.id AS id, labels(t) AS labels, t.name AS name, t.qualified_name AS qualified_name,
       t.path AS path, t.start_line AS start_line, t.end_line AS end_line,
       t.source_commit AS source_commit, t.committed_at AS committed_at,
       r.edge_kind AS edge_kind
ORDER BY id
LIMIT $lim
"""

# ALWAYS emitted (the overlay's honesty axis, the runtime peer of _STATIC_LOWER_BOUND_GAP):
# the overlay is opt-in + HEAD-only, so an absent COVERS edge means "this test run did not
# measure it (or none was loaded)", NEVER "no test covers it" — and it is an AUGMENT of, not
# a replacement for, the static channel.
_RUNTIME_OVERLAY_GAP = (
    "runtime coverage overlay is opt-in and HEAD-only (COVERS edges are materialised from an "
    "external producer's per-test coverage against the built HEAD): an empty or short result "
    "means 'not measured by the coverage producer', not 'no test covers it'. This AUGMENTS the "
    "static CALLS test-impact channel — it does not replace it"
)


def recall_runtime_test_impact(driver, method_id, limit=25):
    """Recall the test Methods OBSERVED at runtime to cover a production Method (ADR-20260714).

    A SEPARATE, read-only overlay entry point that AUGMENTS :func:`recall_test_impact`. Reads
    the ``COVERS`` (``edge_kind='runtime'``) edges the :mod:`palimpsest.kg.coverage` loader
    materialised from a producer's per-test coverage — one backward hop from the production
    ``method_id`` to its covering test Methods (runtime coverage is a direct observation, not a
    transitive relation, so there is no BFS). Each item is tagged ``relation=COVERS`` and
    carries ``edge_kind='runtime'`` so an overlay result is PROVENANCE-DISTINCT from a static
    ``relation=CALLS`` one and the two channels never merge. Same bounded
    ``{items, sources, summaries, gaps, confidence, expand_handle}`` shape (author-omitted via
    :func:`_item` / :func:`_sources`); combinatorial only, no LLM, no build.

    Gaps: the opt-in / HEAD-only disclosure (:data:`_RUNTIME_OVERLAY_GAP`) is ALWAYS present —
    absence of a COVERS edge is 'not measured', never 'no test covers it'. This overlay does
    NOT carry the static lower-bound gap; that stays on the static channel it augments.
    """
    gaps = [_RUNTIME_OVERLAY_GAP]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_RUNTIME_TEST_CALLERS, id=method_id, lim=limit)]
    items = [{**_item(r, COVERS, 1), "edge_kind": r.get("edge_kind") or EDGE_KIND_RUNTIME}
             for r in rows]
    if not items:
        gaps.append(
            f"no runtime COVERS caller for '{method_id}' — see the overlay disclosure above"
        )
    return _result(items, gaps, None, [])


# ── edge-precision: the resolution='name' recall channel ──────────────────────
# "Which Java references did the extractor resolve by NAME (not by type)?" A
# dedicated, global read-only entry point that CONSUMES the per-edge
# ``resolution`` marker (Edge.resolution, projected onto CALLS / DEPENDS_ON in
# kg/ingest.py) and surfaces the name-resolved edges as its own grounded channel.
# Detect-only: it re-uses the existing marker, never re-derives precision. Mirrors
# the sibling MODIFIES channels (recall_churn / recall_cochange): SEPARATE global
# entry point, standard result shape, uncapped (no fan-out cap — a whole-graph
# scan bounded only by ``limit``, exactly like recall_churn).

# CALLS / DEPENDS_ON edges the extractor stamped ``resolution='name'`` whose SOURCE
# endpoint lives in a ``.java`` file. Only SCALAR projections (``AS`` columns) are
# returned — never a whole node — so the per-node author (stamped on every Method /
# Class) cannot leak. ``$lim`` is the sole parameter (server-side LIMIT after a total
# ORDER BY, rebuild-deterministic); ``.java`` / ``name`` are DEV literals baked into
# the query text (trusted constants, not caller input), so no untrusted value is ever
# interpolated. ``type(r)`` distinguishes CALLS from DEPENDS_ON per row.
_EDGE_PRECISION = """
MATCH (a)-[r:CALLS|DEPENDS_ON]->(b)
WHERE r.resolution = 'name' AND a.path ENDS WITH '.java'
RETURN a.id AS id, labels(a) AS labels, a.name AS name,
       a.qualified_name AS qualified_name,
       a.path AS path, a.start_line AS start_line, a.end_line AS end_line,
       a.source_commit AS source_commit, a.committed_at AS committed_at,
       type(r) AS relation_type, r.resolution AS resolution, b.id AS dst
ORDER BY id, relation_type, dst
LIMIT $lim
"""

# Standing gap (ac-2 honesty), ALWAYS emitted: name-resolution is a LOW-PRECISION
# marker, NOT a defect verdict, and the two relation kinds mean different things. A
# Java DEPENDS_ON is STRUCTURALLY always resolution='name' (the extractor never
# type-resolves import targets — see extract/java.py), so those flags are the norm,
# not a finding; a CALLS resolution='name' is a MEANINGFUL name-fallback (the callee
# could not be bound to a typed Method). Completeness is NOT claimed — only edges the
# extractor stamped are flagged, not every imprecise reference.
_EDGE_PRECISION_GAP = (
    "resolution='name' is a LOW-PRECISION marker, not a quality verdict: a Java "
    "DEPENDS_ON is STRUCTURALLY always resolution='name' (import targets are never "
    "type-resolved), so those flags are the norm; a CALLS resolution='name' is a "
    "meaningful name-fallback (the callee could not be bound to a typed Method). "
    "Completeness is NOT claimed — only edges the extractor stamped are flagged"
)


def _edge_precision_reingest_gap() -> str:
    """The DISTINCT empty-result advisory: an exact ``resolution='name'`` match is
    empty BOTH when the graph is genuinely clean AND when it PREDATES the per-edge
    resolution marker (its CALLS / DEPENDS_ON edges carry no resolution property at
    all). Emitting this on empty stops a predate-empty from reading as a false
    all-clear — mirrors :func:`_test_impact_reingest_gap`."""
    return (
        "no resolution='name' edge found on any .java endpoint; if unexpected, the "
        "graph may PREDATE the per-edge resolution marker (its CALLS / DEPENDS_ON "
        "edges carry no resolution property, so an exact resolution='name' match is "
        "empty) — re-ingest to populate resolution before reading this as 'no "
        "low-precision edges'"
    )


def recall_edge_precision(driver, limit=25):
    """Recall the name-resolved (low-precision) Java edges — the ``resolution='name'``
    channel.

    A SEPARATE, global read-only entry point that CONSUMES the per-edge
    ``resolution`` marker (never re-derives it). MATCHes CALLS / DEPENDS_ON edges
    stamped ``resolution='name'`` whose source endpoint is a ``.java`` node,
    id-ordered before a server-side ``LIMIT $lim`` (rebuild-deterministic) with
    ``$lim`` the sole parameter — ``.java`` / ``name`` are dev literals, never
    interpolated caller input. Each item is the grounded SOURCE endpoint (commit +
    file:line via :func:`_sources`, author-omitted — never a whole-node projection)
    carrying its ``relation`` kind (CALLS vs DEPENDS_ON, so the structurally-always-low
    DEPENDS_ON is distinguishable from a meaningful CALLS name-fallback) and its
    ``resolution``. NO content-verdict field — this is a grounded observation, not a
    quality judgment; ``confidence`` is the deterministic grounding-coverage share
    (:func:`_confidence`). Combinatorial only (one scan + dict building), no LLM.

    Gaps (ac-2 / ac-3 honesty): the low-precision note is ALWAYS present (completeness
    never claimed, DEPENDS_ON-vs-CALLS distinguished); on an EMPTY result a DISTINCT
    re-ingest advisory is added so a graph predating the resolution marker is not
    mistaken for a clean 'no low-precision edges'.
    """
    with driver.session() as session:
        rows = [r.data() for r in session.run(_EDGE_PRECISION, lim=limit)]
    items = []
    for rec in rows:
        it = _item(rec, rec["relation_type"], 1)
        it["resolution"] = rec["resolution"]
        it["dst"] = rec["dst"]
        items.append(it)
    gaps = [_EDGE_PRECISION_GAP]
    if not items:
        gaps.append(_edge_precision_reingest_gap())
    return _result(items, gaps, None, [])


# ── callgraph-locality: the cross-package CALLS recall channel ────────────────
# "Of a Java Method's outgoing typed CALLS, what share leave its own Package?" A
# dedicated, global read-only entry point (facet-2) that COMPOSES with the facet-1
# ``resolution`` marker: locality is computed over TYPED calls only, and the
# name-collision noise (resolution='name' CALLS fan out to every same-simple-name
# method corpus-wide) is carried in a SEPARATE count, never in the cross numerator.
# BOUNDARY = Package (NOT Community — Community membership is a deterministic
# structural partition that is definitionally vacuous for this cross-boundary
# question; anchoring here on the real Package spine is what avoids the constant-zero
# degenerate). Detect-only: it re-uses the CONTAINS spine + the resolution marker,
# never re-derives either. Mirrors recall_edge_precision / recall_churn: SEPARATE
# global entry point, standard result shape, uncapped (whole-graph scan bounded only
# by ``limit``).
#
# Per Java caller Method the query aggregates its outgoing CALLS into the TRIPLE
# (cross / same / unresolved) + a separate name_calls, one row per caller (id-ordered
# before a server-side ``LIMIT $lim``, rebuild-deterministic; ``$lim`` the sole
# parameter — ``.java`` / ``typed`` / ``name`` are DEV literals, never interpolated).
# The Method->Package climb is a VARIABLE-LENGTH ``[:CONTAINS*]`` upward (Package ->
# File -> Class -> ... -> Method): nested classes add Class->Class hops, so a fixed
# path length would silently drop them. SCOPE is Java via ``caller.path ENDS WITH
# '.java'`` (METHOD nodes carry no language tag, and Kotlin/others also emit Package
# CONTAINS, so the path filter is the language boundary). BRANCH (ADR-20260703): no
# explicit branch filter — exactly like recall_edge_precision — because node ids are
# branch-scoped and CONTAINS / CALLS edges connect only same-branch nodes, so the
# per-caller aggregation (keyed by the branch-scoped ``caller.id``) and its CONTAINS
# climb stay within one branch plane; cross-branch nodes cannot be double-counted.
# Only SCALAR projections are returned (never a whole node), so the per-node author
# cannot leak. A callee with NO resolvable Package (``dp IS NULL``) is counted as its
# own ``unresolved`` bucket — NEVER folded into ``same`` via a NULL comparison — and a
# DEFAULT-PACKAGE caller (``cp IS NULL``, ``_package_fqn==''`` -> no Package node) is
# flagged ``default_package`` with cross==same==0 (no computable locality), NEVER
# collapsed as same-package. cross_ratio is guarded (0/0 -> 0.0, never NaN).
_CALLGRAPH_LOCALITY = """
MATCH (caller:Method)-[c:CALLS]->(callee)
WHERE caller.path ENDS WITH '.java'
OPTIONAL MATCH (cp:Package)-[:CONTAINS*]->(caller)
OPTIONAL MATCH (dp:Package)-[:CONTAINS*]->(callee)
WITH caller, cp,
     c.resolution AS res,
     dp.qualified_name AS callee_pkg
WITH caller, cp,
     count(CASE WHEN res = 'typed' AND callee_pkg IS NOT NULL
                 AND callee_pkg <> cp.qualified_name THEN 1 END) AS cross,
     count(CASE WHEN res = 'typed' AND callee_pkg IS NOT NULL
                 AND callee_pkg = cp.qualified_name THEN 1 END) AS same,
     count(CASE WHEN res = 'typed' AND callee_pkg IS NULL THEN 1 END) AS unresolved,
     count(CASE WHEN res = 'name' THEN 1 END) AS name_calls
RETURN caller.id AS id, labels(caller) AS labels, caller.name AS name,
       caller.qualified_name AS qualified_name,
       caller.path AS path, caller.start_line AS start_line, caller.end_line AS end_line,
       caller.source_commit AS source_commit, caller.committed_at AS committed_at,
       cross, same, unresolved, name_calls,
       (cp IS NULL) AS default_package,
       CASE WHEN (cross + same) > 0
            THEN toFloat(cross) / (cross + same) ELSE 0.0 END AS cross_ratio
ORDER BY id
LIMIT $lim
"""

# Standing gap (ac-2 / ac-3 honesty), ALWAYS emitted: cross_ratio is a grounded
# OBSERVATION over TYPED cross-package calls, not a quality verdict, and it is a
# LOWER-completeness view by construction. (1) Methods with ZERO outgoing CALLS are
# ABSENT from the result — absence is NOT high locality. (2) resolution='name'
# name-collision calls are carried in name_calls, kept OUT of the cross numerator, so
# name-fallback noise never inflates locality. (3) callees with no resolvable Package
# are their own ``unresolved`` bucket, excluded from the cross/same denominator, never
# counted as same-package. (4) DEFAULT-PACKAGE callers (no Package node) are flagged
# default_package with no computable locality, never collapsed as same-package.
# Completeness is NOT claimed.
_CALLGRAPH_LOCALITY_GAP = (
    "cross_ratio is a grounded observation over TYPED cross-package CALLS, not a "
    "quality verdict: Methods with zero outgoing CALLS are ABSENT (absence is not "
    "high locality); resolution='name' name-collision calls are carried in name_calls "
    "and kept OUT of the cross numerator; callees with no resolvable Package are their "
    "own unresolved bucket (excluded from the denominator, never same-package); a "
    "default-package caller has no computable locality (default_package). Completeness "
    "is NOT claimed"
)


def _callgraph_locality_reingest_gap() -> str:
    """The DISTINCT empty-result advisory: an empty result is ambiguous — it can mean
    a genuinely CALLS-free / non-Java graph OR a graph that PREDATES the CALLS +
    per-edge resolution + Package CONTAINS spine this channel reads. Emitting this on
    empty stops a predate-empty from reading as a false all-clear — mirrors
    :func:`_edge_precision_reingest_gap`."""
    return (
        "no Java caller with outgoing typed CALLS found; if unexpected, the graph may "
        "PREDATE the CALLS + per-edge resolution + Package CONTAINS spine this channel "
        "reads (its edges carry no resolution property, or Package nodes were not "
        "minted) — re-ingest before reading this as 'perfect locality'"
    )


def recall_callgraph_locality(driver, limit=25):
    """Recall Java callers by cross-package CALLS locality — the facet-2 signal.

    A SEPARATE, global read-only entry point that COMPOSES with the per-edge
    ``resolution`` marker (facet-1): per Java caller Method it aggregates the outgoing
    CALLS into the grounded TRIPLE — ``cross`` (typed calls to a DIFFERENT Package),
    ``same`` (typed calls within the caller's Package), ``unresolved`` (typed calls
    whose callee has no resolvable Package) — plus ``name_calls`` (resolution='name'
    name-collision calls carried SEPARATELY, never in the cross numerator) and
    ``cross_ratio`` = cross / (cross + same), guarded to 0.0 (never a 0/0 NaN). The
    Method->Package boundary is a VARIABLE-LENGTH CONTAINS climb (nested classes add
    hops). Scoped to Java by the ``.java`` caller path; id-ordered before a server-side
    ``LIMIT $lim`` (``$lim`` the sole parameter, rebuild-deterministic). Each item is
    the grounded CALLER (commit + file:line via :func:`_sources`, author-omitted —
    scalar projections only, never a whole node). NO content-verdict field — a grounded
    observation, not a quality judgment. Combinatorial only (one aggregation + dict
    building), no LLM.

    Gaps (ac-2 / ac-3 honesty): the standing gap is ALWAYS present (zero-CALLS absence,
    name-collision split, unresolved bucket, default-package bucket — completeness never
    claimed); on an EMPTY result a DISTINCT re-ingest advisory is added so a graph
    predating the CALLS / resolution / CONTAINS spine is not mistaken for perfect
    locality.
    """
    with driver.session() as session:
        rows = [r.data() for r in session.run(_CALLGRAPH_LOCALITY, lim=limit)]
    items = []
    for rec in rows:
        it = _item(rec, CALLS, 1)
        it["cross"] = rec["cross"]
        it["same"] = rec["same"]
        it["unresolved"] = rec["unresolved"]
        it["name_calls"] = rec["name_calls"]
        it["default_package"] = rec["default_package"]
        it["cross_ratio"] = rec["cross_ratio"]
        items.append(it)
    gaps = [_CALLGRAPH_LOCALITY_GAP]
    if not items:
        gaps.append(_callgraph_locality_reingest_gap())
    return _result(items, gaps, None, [])


# ── composite refactor-candidate identifier: facet-1 ∧ facet-2 ────────────────
# The DETERMINISTIC, provider-free SELECTOR (facet-3 / wi_260714ns9 M1+M3) that
# COMPOSES the two Relate signals in ONE id-ordered query: per Java caller Method it
# aggregates BOTH the name-resolution axis (name_resolved CALLS — LOW PRECISION) AND
# the locality axis (cross-package typed CALLS — LOW LOCALITY), then keeps ONLY the
# Methods carrying BOTH (``cross > 0 AND name_calls > 0``). This is the SINGLE source
# of the composite predicate (M2: curate re-implements NONE of it) and a SINGLE
# id-ordered query — NEVER an intersection of two independently-capped recalls, whose
# re-run target set would be non-deterministic (M3).
#
# Each surviving Method is emitted as an EXTRACTION TARGET tuple for the host-injected
# LLM producer: ``id`` (target), ``grounding_ids`` (the target plus the callee ids its
# composite calls reach — the CLOSED citation set), and a NEUTRAL ``facts`` triple
# (raw cross/same/unresolved/name_calls counts, NO adjectives). The LLM SELECTS
# NOTHING — the selection IS this deterministic query (F-Q6); the model only
# synthesises a grounded co-occurrence OBSERVATION over the pre-selected tuple.
#
# Reuses the facet-2 aggregation shape verbatim (the ``[:CONTAINS*]`` variable-length
# Method->Package climb, the resolution marker, the ``.java`` language boundary,
# branch-scoped ids). Combinatorial only, no LLM. Scalar projections only (never a
# whole node), so the per-node author cannot leak. ``$lim`` is the SOLE parameter;
# ``.java`` / ``typed`` / ``name`` are DEV literals, never interpolated.
_REFACTOR_CANDIDATES = """
MATCH (caller:Method)-[c:CALLS]->(callee)
WHERE caller.path ENDS WITH '.java'
OPTIONAL MATCH (cp:Package)-[:CONTAINS*]->(caller)
OPTIONAL MATCH (dp:Package)-[:CONTAINS*]->(callee)
WITH caller, cp, c.resolution AS res, callee,
     dp.qualified_name AS callee_pkg
WITH caller, cp,
     count(CASE WHEN res = 'typed' AND callee_pkg IS NOT NULL
                 AND callee_pkg <> cp.qualified_name THEN 1 END) AS cross,
     count(CASE WHEN res = 'typed' AND callee_pkg IS NOT NULL
                 AND callee_pkg = cp.qualified_name THEN 1 END) AS same,
     count(CASE WHEN res = 'typed' AND callee_pkg IS NULL THEN 1 END) AS unresolved,
     count(CASE WHEN res = 'name' THEN 1 END) AS name_calls,
     collect(DISTINCT CASE
         WHEN res = 'name'
              OR (res = 'typed' AND callee_pkg IS NOT NULL
                  AND callee_pkg <> cp.qualified_name)
         THEN callee.id END) AS raw_grounding
WITH caller, cross, same, unresolved, name_calls,
     [x IN raw_grounding WHERE x IS NOT NULL] AS grounding
WHERE cross > 0 AND name_calls > 0
RETURN caller.id AS id, labels(caller) AS labels, caller.name AS name,
       caller.qualified_name AS qualified_name,
       caller.path AS path, caller.start_line AS start_line, caller.end_line AS end_line,
       caller.source_commit AS source_commit, caller.committed_at AS committed_at,
       cross, same, unresolved, name_calls, grounding
ORDER BY id
LIMIT $lim
"""

# Standing gap (ac-2 honesty), ALWAYS emitted: a candidate is the CO-OCCURRENCE of two
# structural facts (a name_resolved CALL AND a cross-package CALL on one Method), NOT a
# refactor verdict — it is a target for grounded external SYNTHESIS, not a judgment. The
# facts triple carries raw counts only; any interpretation is the reader's. Completeness
# is NOT claimed (Methods missing either axis are absent — absence is not a clean bill).
_REFACTOR_CANDIDATES_GAP = (
    "a candidate is the grounded CO-OCCURRENCE of two structural facts — a "
    "name-resolved (low-precision) CALL AND a cross-package (low-locality) CALL on one "
    "Method — surfaced as a target for external grounded synthesis, NOT a refactor "
    "verdict or quality judgment; the facts triple is raw counts only. Methods carrying "
    "just one axis are absent (absence is not a clean bill). Completeness is NOT claimed"
)


def _refactor_candidates_absent_gap() -> str:
    """The DISTINCT empty-result advisory: an empty composite is AMBIGUOUS — it can mean
    the two axes genuinely never co-occur on one Method on a resolved corpus (the
    documented current state — per-edge precision leaves few/no name-resolved CALLS) OR
    a graph that PREDATES the CALLS + resolution + Package CONTAINS spine this reads.
    Emitting this on empty stops an empty from reading as a false 'no such coupling' —
    mirrors :func:`_callgraph_locality_reingest_gap`."""
    return (
        "no Method carries BOTH a name-resolved CALL and a cross-package CALL; this "
        "empty is ambiguous — on a PRECISION-RESOLVED corpus name-resolved CALLS are "
        "few or absent so the two axes rarely co-occur (the marker for this composite "
        "may be structurally ABSENT), OR the graph PREDATES the CALLS + resolution + "
        "Package CONTAINS spine — re-ingest before reading this as 'no such coupling'"
    )


def _candidate_facts(rec) -> str:
    """The NEUTRAL facts triple for the producer: raw counts + resolution marker, NO
    adjectives (F-Q6 framing — the model synthesises an observation, it is not handed a
    verdict). Deterministic string of the aggregated counts."""
    return (
        f"typed_cross_package_calls={rec['cross']}; "
        f"typed_same_package_calls={rec['same']}; "
        f"typed_unresolved_calls={rec['unresolved']}; "
        f"name_resolved_calls={rec['name_calls']}"
    )


def recall_refactor_candidates(driver, limit=25):
    """Identify composite refactor CANDIDATES — Java Methods carrying BOTH the
    low-precision (name_resolved CALLS) AND low-locality (cross-package CALLS) axes —
    and emit each as a grounded EXTRACTION TARGET for the host-injected LLM producer
    (facet-3 / wi_260714ns9 M1+M3).

    A SEPARATE, global read-only entry point that COMPOSES the two Relate signals in a
    SINGLE deterministic, provider-free, id-ordered query (never an intersection of two
    capped recalls). Per surviving Method it returns the standard result shape whose
    items each additionally carry ``grounding_ids`` (the target plus the callee ids its
    composite calls reach — the closed citation set the producer grounds in) and a
    NEUTRAL ``facts`` triple (raw cross/same/unresolved/name_calls counts, no
    adjectives). The LLM SELECTS nothing — the selection IS this query (F-Q6). Each item
    is the grounded CALLER (commit + file:line via :func:`_sources`, author-omitted —
    scalar projections only). NO content-verdict field. Combinatorial only, no LLM.

    Gaps (ac-2 honesty): the standing gap is ALWAYS present (co-occurrence-not-verdict,
    completeness never claimed); on an EMPTY result a DISTINCT absent/predate advisory is
    added so an empty composite is not mistaken for 'no such coupling' — on a
    precision-resolved corpus the composite is legitimately empty (documented vacuity),
    which is distinct from a graph predating the spine."""
    with driver.session() as session:
        rows = [r.data() for r in session.run(_REFACTOR_CANDIDATES, lim=limit)]
    items = []
    for rec in rows:
        it = _item(rec, CALLS, 1)
        it["grounding_ids"] = (rec["id"], *rec["grounding"])
        it["facts"] = _candidate_facts(rec)
        it["cross"] = rec["cross"]
        it["same"] = rec["same"]
        it["unresolved"] = rec["unresolved"]
        it["name_calls"] = rec["name_calls"]
        items.append(it)
    gaps = [_REFACTOR_CANDIDATES_GAP]
    if not items:
        gaps.append(_refactor_candidates_absent_gap())
    return _result(items, gaps, None, [])
