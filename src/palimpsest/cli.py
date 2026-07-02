"""Command-line entry for palimpsest: extract -> ingest and grounded recall.

Two stdlib-argparse subcommands wire the deterministic slice end to end:

  ingest --repo PATH [--commit HEAD]
      Extract the Java tree under PATH + read git provenance for the pinned
      commit, then create_constraints + ingest the IR into Neo4j.

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

from palimpsest.extract import extract, read_provenance
from palimpsest.ir import Summary
from palimpsest.kg import augment_communities, create_constraints, ingest, load_summaries
from palimpsest.recall import recall

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


def _cmd_query(args) -> None:
    with _driver() as driver:
        result = recall(driver, args.symbol, depth=args.depth, limit=args.limit)
    _print_result(args.symbol, args.depth, args.limit, result)


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
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
