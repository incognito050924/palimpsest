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

from palimpsest.ir import (
    CALLS,
    DEPENDS_ON,
    CONTAINS,
    IMPORTS,
    MEMBER_OF,
    INFERRED_RELATION_TYPES,
)

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
