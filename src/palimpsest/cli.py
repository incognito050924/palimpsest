"""Command-line entry for palimpsest: extract -> ingest and grounded recall.

Two stdlib-argparse subcommands wire the deterministic slice end to end:

  ingest --repo PATH [--commit HEAD]
      Extract the Java tree under PATH + read git provenance for the pinned
      commit, then create_constraints + ingest the IR into Neo4j.

  backfill --repo PATH
      Replay that same extract -> ingest over the FULL git history (every commit,
      oldest -> newest) so the projection reflects the whole timeline, not just
      HEAD. Provider-free, deterministic, idempotent; leaves the repo untouched.

  query SYMBOL_OR_FILE [--depth N] [--limit M]
      Run bounded, grounded recall from a seed (a symbol qualified_name or a
      repo-relative file path) and print the result as clearly SEPARATED
      sections — items (each with its commit + file:line source), inferred
      summaries (in their own channel, never merged into items), gaps, and
      confidence — never a merged prose answer.

  load PAYLOAD.json | PAYLOAD_DIR/
      Load externally-produced summary JSON payloads (each an array of summary
      objects) into the inferred layer via grounded, summary-atomic load. Accepts
      a single file OR a DIRECTORY — the directory is the git-tracked
      source-of-truth (git = SoT, Neo4j = re-buildable projection), so every
      *.json inside it is batch-loaded and a dropped Neo4j can be rebuilt by
      re-running load (deterministic ids make it idempotent). Prints the loaded
      count and EVERY rejection reason — rejections are surfaced, never silently
      dropped. palimpsest calls no model; payloads are generated elsewhere.

The Neo4j connection comes from the environment (localhost defaults):
  NEO4J_URI (bolt://localhost:7687), NEO4J_USER (neo4j), NEO4J_PASSWORD (neo4j).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from neo4j import GraphDatabase

from palimpsest.backfill import backfill
from palimpsest.extract import extract, read_provenance
from palimpsest.ir import Summary
from palimpsest.kg import augment_communities, create_constraints, ingest, load_summaries
from palimpsest.recall import recall
from palimpsest.recall.graphrag import reconcile_recall
from palimpsest.reconcile import capture

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j"


@contextmanager
def _driver():
    uri = os.environ.get("NEO4J_URI", DEFAULT_URI)
    user = os.environ.get("NEO4J_USER", DEFAULT_USER)
    password = os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        yield driver
    finally:
        driver.close()


def _cmd_ingest(args) -> None:
    prov = read_provenance(args.repo, args.commit)
    ir = extract(args.repo, prov)
    # Materialize the deterministic Class-level Community partition into the IR so
    # the generic ingest writers persist the Community nodes + MEMBER_OF edges.
    augment_communities(ir, prov)
    with _driver() as driver:
        create_constraints(driver)
        ingest(driver, ir)
    print(
        f"ingested {len(ir.nodes)} nodes, {len(ir.edges)} edges "
        f"from {args.repo} @ {prov.source_commit}"
    )


def _cmd_backfill(args) -> None:
    with _driver() as driver:
        result = backfill(driver, args.repo)
    print(
        f"backfilled {result.commits} commits from {args.repo} "
        f"({result.nodes} nodes, {result.edges} edges at HEAD)"
    )


def _cmd_query(args) -> None:
    with _driver() as driver:
        result = recall(driver, args.symbol, depth=args.depth, limit=args.limit)
    _print_result(args.symbol, args.depth, args.limit, result)


def _cmd_reconcile(args) -> int:
    # The explicit branch set IS the comparison scope (ac-6): only these branches
    # are captured + compared; unspecified branches never enter the query.
    branches = args.branch or []
    if not branches:
        # N=0: defined, honest behavior — never a crash. There is nothing to
        # compare without at least one branch to scope the comparison to.
        print(
            "reconcile: no branch specified; pass at least one --branch to "
            "define the comparison scope"
        )
        return 2
    with _driver() as driver:
        try:
            # capture() validates the branch set git-safely (--end-of-options /
            # --verify), dedups, and fails closed on a shallow repo; a bad branch
            # or shallow repo raises. Catch it here so the CLI prints an honest
            # error instead of leaking a raw Python traceback.
            capture(driver, args.repo, branches)
            result = reconcile_recall(
                driver, args.symbol, branches, limit=args.limit
            )
        except (ValueError, RuntimeError) as exc:
            print(f"reconcile error: {exc}")
            return 2
    _print_reconcile(args.symbol, result)
    return 0


def _semantic_annotations(semantic) -> list[str]:
    """Flatten the DISPLAY-ONLY inferred layer bound to a peer into terse
    (verdict, confidence, source) lines. palimpsest generates none of it — this
    only surfaces what an external judge already loaded. Author-omitted."""
    lines = []
    for channel in ("summaries", "risks", "decisions", "relations"):
        for entry in semantic.get(channel, []):
            verdict = entry.get("semantic_verdict")
            has_conf = entry.get("confidence") is not None
            if verdict is None and not has_conf:
                continue
            v = verdict.get("verdict") if isinstance(verdict, dict) else verdict
            src = entry.get("source_commit") or entry.get("code_bound_at")
            lines.append(
                f"verdict={v} confidence={entry.get('confidence')} source={src}"
            )
    return lines


def _print_reconcile(symbol, result) -> None:
    # SEPARATED sections (mirrors _print_result): per-branch PEERS, then the
    # computed CODE DIVERGENCE, then the surfaced CONFLICTS, then GAPS — never a
    # merged prose answer, and no privileged branch.
    peers = result["peers"]
    branches = result["branches"]
    print(f"RECONCILE: {symbol}  (branches={', '.join(branches)})")
    print()
    print(f"PEERS ({len(peers)})")
    if not peers:
        print("  (none)")
    for p in peers:
        rank = "freshest" if p["freshest"] else "older"
        print(f"  - [{p['branch']}] {p['qualified_name'] or p['id']}  ({rank})")
        print(f"      source: {_fmt_source(p['sources'])}")
        for ann in _semantic_annotations(p["semantic"]):
            print(f"      semantic: {ann}")
    print()
    div = result["code_divergence"]
    print(f"CODE DIVERGENCE: diverged={div['diverged']}")
    for c in div["source_commits"]:
        print(f"  - {c}")
    print()
    conflicts = result["conflict_edges"]
    print(f"CONFLICTS ({len(conflicts)})")
    if not conflicts:
        print("  (none)")
    for e in conflicts:
        print(f"  - {e['source_id']} CONFLICTS_WITH {e['target_id']}")
    print()
    gaps = result["gaps"]
    print(f"GAPS ({len(gaps)})")
    if not gaps:
        print("  (none)")
    for g in gaps:
        print(f"  - {g}")


def _read_payload_file(path) -> list:
    with open(path, encoding="utf-8") as f:
        return [Summary.from_dict(d) for d in json.load(f)]


def _cmd_load(args) -> None:
    # A DIRECTORY is the git-tracked source-of-truth: batch-load every *.json
    # payload file inside it (sorted, deterministic) so a Neo4j drop can be
    # rebuilt from git. A single file keeps the original one-payload behavior.
    if os.path.isdir(args.payload):
        summaries = [
            s for p in sorted(Path(args.payload).glob("*.json"))
            for s in _read_payload_file(p)
        ]
    else:
        summaries = _read_payload_file(args.payload)
    with _driver() as driver:
        result = load_summaries(driver, summaries)
    _print_load_result(args.payload, result)


def _print_load_result(path, result) -> None:
    print(f"LOADED {result.loaded}/{result.intended} summaries from {path}")
    print(f"REJECTED ({result.rejected})")
    for rej in result.rejections:
        print(f"  - {rej.summary_id}: {rej.reason}")


def _fmt_source(src) -> str:
    commit = src.get("source_commit") or "?"
    path = src.get("path")
    if not path:
        return f"{commit} (no file grounding)"
    return f"{commit} {path}:{src.get('start_line')}-{src.get('end_line')}"


def _print_result(symbol, depth, limit, result) -> None:
    items = result["items"]
    print(f"QUERY: {symbol}  (depth={depth}, limit={limit})")
    print()
    print(f"ITEMS ({len(items)})")
    if not items:
        print("  (none)")
    for it in items:
        role = "seed" if it["relation"] is None else f"via {it['relation']} @depth {it['depth']}"
        print(f"  - {it['kind']} {it['qualified_name'] or it['id']}  [{role}]")
        print(f"      source: {_fmt_source(it['sources'])}")
    print()
    summaries = result.get("summaries", [])
    print(f"SUMMARIES ({len(summaries)})")
    if not summaries:
        print("  (none)")
    for s in summaries:
        print(f"  - inferred summary of {s['target_id']}  [bound {s['code_bound_at']}]")
        for c in s["claims"]:
            refs = ", ".join(c.get("source_refs", []))
            print(f"      {c['text']}  (grounds: {refs})")
    print()
    gaps = result["gaps"]
    print(f"GAPS ({len(gaps)})")
    if not gaps:
        print("  (none)")
    for g in gaps:
        print(f"  - {g}")
    print()
    print(f"CONFIDENCE: {result['confidence']}")
    if result.get("expand_handle"):
        frontier = result["expand_handle"].get("frontier", [])
        print(f"MORE: {len(frontier)} frontier node(s) not expanded (bounded)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="palimpsest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser(
        "ingest", help="extract a Java tree + provenance and ingest into Neo4j"
    )
    p_ing.add_argument("--repo", required=True, help="path to the Java repository root")
    p_ing.add_argument(
        "--commit", default="HEAD", help="git commit to pin provenance to (default HEAD)"
    )
    p_ing.set_defaults(func=_cmd_ingest)

    p_bf = sub.add_parser(
        "backfill",
        help="replay extract+ingest over the FULL git history (every commit)",
    )
    p_bf.add_argument("--repo", required=True, help="path to the Java repository root")
    p_bf.set_defaults(func=_cmd_backfill)

    p_q = sub.add_parser(
        "query", help="grounded recall for a symbol or repo-relative file path"
    )
    p_q.add_argument("symbol", help="a symbol qualified_name or repo-relative file path")
    p_q.add_argument("--depth", type=int, default=1, help="max traversal hops (default 1)")
    p_q.add_argument("--limit", type=int, default=25, help="max items (default 25)")
    p_q.set_defaults(func=_cmd_query)

    p_load = sub.add_parser(
        "load",
        help="load an externally-produced summary JSON payload (provider-free)",
    )
    p_load.add_argument(
        "payload",
        help="a JSON file (array of summary objects) OR a directory of such "
        "files — the git-tracked source-of-truth to rebuild Neo4j from",
    )
    p_load.set_defaults(func=_cmd_load)

    p_rec = sub.add_parser(
        "reconcile",
        help="N-way branch comparison for one symbol (capture then compare "
        "branch-scoped peers as equals, sections kept separate)",
    )
    p_rec.add_argument("symbol", help="the target symbol qualified_name to compare")
    p_rec.add_argument(
        "--branch", action="append", metavar="BRANCH",
        help="a branch in the comparison scope; repeat for N branches. The "
        "explicit set IS the scope — unspecified branches are excluded.",
    )
    p_rec.add_argument("--repo", required=True, help="path to the git repository to capture")
    p_rec.add_argument("--limit", type=int, default=25, help="max peers per branch (default 25)")
    p_rec.set_defaults(func=_cmd_reconcile)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # Existing subcommands return None (-> 0, byte-identical); reconcile returns an
    # explicit exit code so honest rejections (bad/missing branch) are non-zero.
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
