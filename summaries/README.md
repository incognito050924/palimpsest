# summaries/ — git-tracked summary source-of-truth

This directory is the **source-of-truth** for externally-produced semantic
summaries (git = SoT, Neo4j = re-buildable projection; ADR-20260626 #2).

- Each file is a `*.json` array of summary objects (the wire shape of
  `ir.Summary.to_dict` — `target_id`, `claims`, `generator`, `model`,
  `source_commit`, `created_at`, optional `prompt`/`confidence`).
- Rebuild the inferred layer after a Neo4j drop:

  ```
  python -m palimpsest load summaries/
  ```

  Every `*.json` here is batch-loaded via the grounded, summary-atomic loader.
  Summary ids are deterministic (`summary:<sha256>`), so re-running is
  idempotent (MERGE-by-id) — the same nodes and `SUMMARIZES` edges are restored.

The `load` path calls no model — payloads are pre-materialized here (git = SoT)
and only then loaded. They are produced either by the isolated opt-in
`palimpsest curate` producer or by an external generator; either way generation
happens before this directory is loaded (ADR-20260706 §결정3: recall+load paths
are LLM-free; generation is isolated in-process or external).
