#!/usr/bin/env python
"""Micro-benchmark of the AS-BUILT palimpsest pipeline (wi_260702sfd).

Measures the spike §4 metrics (docs/spikes/db-substrate-spike.md) for the
pipeline as it exists today — Neo4j substrate, NO vector layer (embeddings are a
deferred C-decision) — against a real corpus. This is NOT the spike's 3-DB
comparison; it records actuals for the single as-built substrate only.

Metrics (provider-free — no LLM, real ``backfill``/``recall`` entry points):
  - Ingest 시간        : full-history backfill from an empty DB (commits/sec)
  - 회상 순회 지연     : recall() over sampled seeds, depth 1 & 2, p50/p95 (ms)
  - 전체 재구축 시간   : DETACH DELETE all + backfill again (teardown + rebuild)
  - 피크 RAM           : peak Neo4j container memory (docker stats) over the run

Usage:
  DITTO_AUTOPILOT_BYPASS=1 .venv/bin/python bench/benchmark.py --repo <corpus> \
      [--seeds 30] [--iters 5] [--out bench/results/asbuilt-neo4j.json]

Requires Docker (spins one ephemeral Neo4j 5 Community testcontainer).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from palimpsest.backfill import backfill
from palimpsest.recall import recall

NEO4J_IMAGE = "neo4j:5-community"
NEO4J_PASSWORD = "palimpsest-bench"


def _configure_docker_endpoint() -> None:
    """Point the docker SDK (testcontainers) at the active daemon.

    Same resolution the test rig uses: Docker Desktop on macOS does not expose
    the default socket, so resolve DOCKER_HOST from the docker CLI context.
    """
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    if os.environ.get("DOCKER_HOST"):
        return
    try:
        host = subprocess.run(
            ["docker", "context", "inspect", "-f",
             "{{.Endpoints.docker.Host}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return
    if host:
        os.environ["DOCKER_HOST"] = host


_MEM_UNITS = {"B": 1 / 1024 / 1024, "KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0,
              "kB": 1000 / 1024 / 1024, "MB": 1000 * 1000 / 1024 / 1024,
              "GB": 1000 * 1000 * 1000 / 1024 / 1024}


def _parse_mem_mib(mem_usage: str) -> float | None:
    """Parse a ``docker stats`` MemUsage cell (e.g. '456.7MiB / 7.6GiB') -> MiB."""
    token = mem_usage.split("/")[0].strip()
    m = re.match(r"([\d.]+)\s*([A-Za-z]+)", token)
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    factor = _MEM_UNITS.get(unit)
    return value * factor if factor is not None else None


class _PeakMemSampler:
    """Poll ``docker stats`` for one container in a background thread; keep max."""

    def __init__(self, container_id: str, interval: float = 0.5):
        self._id = container_id
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.peak_mib = 0.0
        self.samples = 0

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format",
                     "{{.MemUsage}}", self._id],
                    check=True, capture_output=True, text=True, timeout=10,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                out = ""
            mib = _parse_mem_mib(out) if out else None
            if mib is not None:
                self.peak_mib = max(self.peak_mib, mib)
                self.samples += 1
            self._stop.wait(self._interval)

    def __enter__(self) -> "_PeakMemSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in [0,100]). Empty -> 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    rank = -(-int(pct * n) // 100)  # ceil(pct/100 * n), integer-only
    rank = max(1, min(n, rank))
    return ordered[rank - 1]


def _sample_seeds(driver, n: int) -> list[str]:
    """Pull up to ``n`` recall seeds from the ingested graph.

    Methods carry the richest neighbourhood (CALLS/CONTAINS), so seed from
    Method qualified_names — exactly what ``recall`` accepts as a symbol seed.
    """
    with driver.session() as session:
        rows = session.run(
            "MATCH (m:Method) WHERE m.qualified_name IS NOT NULL "
            "RETURN m.qualified_name AS qn ORDER BY qn LIMIT $n",
            n=n,
        )
        return [r["qn"] for r in rows]


def _recall_latencies(driver, seeds: list[str], depth: int,
                      iters: int) -> list[float]:
    """Wall-clock ms per recall() call across seeds × iters."""
    out: list[float] = []
    for seed in seeds:
        for _ in range(iters):
            t0 = time.perf_counter()
            recall(driver, seed, depth=depth, limit=25)
            out.append((time.perf_counter() - t0) * 1000.0)
    return out


def _wipe(driver) -> None:
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


def run(repo: str, n_seeds: int, iters: int,
        total_commits: int | None = None) -> dict:
    from testcontainers.neo4j import Neo4jContainer

    result: dict = {"corpus": os.path.abspath(repo), "substrate": NEO4J_IMAGE,
                    "note": "as-built single-substrate actuals; no vector layer. "
                    "Measured on a recent-commit window (near-HEAD graph = "
                    "worst-case ingest rate); full-history figures are "
                    "rate-extrapolated, not wall-clock.",
                    "full_history_commits": total_commits}
    with Neo4jContainer(NEO4J_IMAGE, password=NEO4J_PASSWORD) as container:
        container_id = container.get_wrapped_container().id
        driver = container.get_driver()
        try:
            driver.verify_connectivity()
            with _PeakMemSampler(container_id) as mem:
                # --- Ingest: full-history backfill from an empty DB ---
                _wipe(driver)
                t0 = time.perf_counter()
                bf = backfill(driver, repo)
                ingest_sec = time.perf_counter() - t0
                result["ingest"] = {
                    "commits": bf.commits,
                    "head_nodes": bf.nodes,
                    "head_edges": bf.edges,
                    "wall_sec": round(ingest_sec, 3),
                    "commits_per_sec": round(bf.commits / ingest_sec, 3)
                    if ingest_sec else None,
                }

                # --- Recall traversal latency: depth 1 & 2 ---
                seeds = _sample_seeds(driver, n_seeds)
                result["recall"] = {"seeds": len(seeds), "iters_per_seed": iters}
                for depth in (1, 2):
                    lat = _recall_latencies(driver, seeds, depth, iters)
                    result["recall"][f"depth{depth}"] = {
                        "samples": len(lat),
                        "p50_ms": round(_percentile(lat, 50), 3),
                        "p95_ms": round(_percentile(lat, 95), 3),
                        "max_ms": round(max(lat), 3) if lat else 0.0,
                    }

                # --- Rebuild: teardown + backfill again (git=SoT rebuild) ---
                t0 = time.perf_counter()
                _wipe(driver)
                teardown_sec = time.perf_counter() - t0
                t0 = time.perf_counter()
                bf2 = backfill(driver, repo)
                rebuild_sec = time.perf_counter() - t0
                result["rebuild"] = {
                    "teardown_sec": round(teardown_sec, 3),
                    "backfill_sec": round(rebuild_sec, 3),
                    "commits": bf2.commits,
                }

                # Extrapolate window rate to the full history (labeled, not
                # wall-clock). Rate is the near-HEAD worst case, so this is a
                # conservative (upper-bound) estimate of full-build seconds.
                if total_commits and bf.commits and ingest_sec:
                    rate = bf.commits / ingest_sec
                    result["extrapolated_full_history"] = {
                        "commits": total_commits,
                        "window_commits_per_sec": round(rate, 3),
                        "est_ingest_sec": round(total_commits / rate, 1),
                        "caveat": "linear extrapolation of a superlinear cost "
                        "(ingest slows as the graph grows — see finding); "
                        "true full-history time is >= this.",
                    }
            result["peak_ram"] = {
                "neo4j_container_mib": round(mem.peak_mib, 1),
                "samples": mem.samples,
            }
        finally:
            driver.close()
    return result


def _print_summary(r: dict) -> None:
    ing, rec, rb, ram = r["ingest"], r["recall"], r["rebuild"], r["peak_ram"]
    print("\n=== palimpsest as-built benchmark (Neo4j, no vector layer) ===")
    print(f"corpus: {r['corpus']}")
    print(f"\n[Ingest] {ing['commits']} commits in {ing['wall_sec']}s "
          f"= {ing['commits_per_sec']} commits/sec "
          f"(HEAD: {ing['head_nodes']} nodes, {ing['head_edges']} edges)")
    print(f"\n[Recall] {rec['seeds']} seeds × {rec['iters_per_seed']} iters")
    for depth in (1, 2):
        d = rec[f"depth{depth}"]
        print(f"  depth {depth}: p50={d['p50_ms']}ms  p95={d['p95_ms']}ms  "
              f"max={d['max_ms']}ms  (n={d['samples']})")
    print(f"\n[Rebuild] teardown {rb['teardown_sec']}s + backfill "
          f"{rb['backfill_sec']}s")
    print(f"\n[Peak RAM] Neo4j container {ram['neo4j_container_mib']} MiB "
          f"({ram['samples']} samples)")
    ext = r.get("extrapolated_full_history")
    if ext:
        print(f"\n[Extrapolated] full history {ext['commits']} commits "
              f"@ {ext['window_commits_per_sec']} c/s ≈ {ext['est_ingest_sec']}s "
              f"ingest (lower bound — cost is superlinear)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="palimpsest as-built micro-benchmark")
    ap.add_argument("--repo", required=True, help="path to the corpus git repo")
    ap.add_argument("--seeds", type=int, default=30, help="recall seed count")
    ap.add_argument("--iters", type=int, default=5, help="iters per seed")
    ap.add_argument("--total-commits", type=int, default=None,
                    help="full-history commit count for rate extrapolation")
    ap.add_argument("--out", default="bench/results/asbuilt-neo4j.json")
    args = ap.parse_args(argv)

    _configure_docker_endpoint()
    result = run(args.repo, args.seeds, args.iters, args.total_commits)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    _print_summary(result)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
