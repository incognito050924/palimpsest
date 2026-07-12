"""palimpsest.curate — the isolated, opt-in generative curator (Candidate B).

THE ONLY region of palimpsest that may call an LLM (ADR-20260706 §결정1). It
reads code/KB facts and produces a summary *payload* (grounding refs + gap +
confidence); the CLI materialises that payload to the git-tracked source-of-truth
and the EXISTING idempotent inferred loader (``kg.summary.load_summaries``)
ingests it unchanged. content-verdict (is this summary true?) is NOT produced
here — judgment stays external (E4).

Isolation invariant (mechanical definition of "isolation"): this package imports
NEITHER ``palimpsest.recall`` NOR the load surface (``palimpsest.kg`` /
``palimpsest.cli``). It therefore sits OUTSIDE the recall/load import closure, so
the path-scoped provider-free probes stay green even with curate installed. Any
real LLM client dependency is imported LAZILY (never at module import), so a
default install stays provider-free and ``import palimpsest.curate`` pulls in no
generative library.
"""

from palimpsest.curate._llm import default_generate
from palimpsest.curate.producer import CurateRequest, produce

__all__ = ["CurateRequest", "produce", "default_generate"]
