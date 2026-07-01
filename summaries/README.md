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

palimpsest calls no model; payloads are generated elsewhere and committed here.
