"""The producer: one grounded request + one LLM response -> one summary payload.

Pure and provider-agnostic. The LLM is injected as ``generate`` (a
``str -> str`` callable), so this module imports no generative library and no
recall/load surface — the real client lives behind the ``curate`` optional
dependency and is constructed by the caller, never at import time.

The payload is a plain dict shaped to the EXISTING ``Summary`` load schema
(``palimpsest.ir.Summary.from_dict``) so the idempotent inferred loader ingests
it unchanged, plus a top-level ``gap`` field that completes the honesty form
(grounding + gap + confidence). content-verdict is deliberately absent — judgment
stays external (E4, ADR-20260706 §결정4).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

# The one identifier the producer must never claim as generator/model: itself.
# curate synthesises with an EXTERNAL model and records that model honestly; it
# never attributes the generation to palimpsest (ADR-20260706 §결정5).
PALIMPSEST_SELF = "palimpsest"


@dataclass(frozen=True)
class CurateRequest:
    """One curate unit: what to summarise, and the REAL ids it may ground in.

    ``grounding_ids`` is the closed set of real graph-node ids (the target plus
    its structural neighbours) a claim is allowed to cite. The producer owns
    grounding — a claim ref outside this set is a hallucinated id and is dropped,
    so the loader never has to reject laundered refs after the fact.
    """

    target_id: str
    grounding_ids: tuple[str, ...]
    facts: str
    source_commit: str
    created_at: str
    generator: str
    model: str
    prompt: Optional[str] = None


def _honest_provenance(generator: str, model: str) -> None:
    """Refuse self-attribution: generator/model must be present and not name
    palimpsest itself (the content came from an external model)."""
    for label, value in (("generator", generator), ("model", model)):
        if not (value and value.strip()):
            raise ValueError(f"curate payload missing {label}")
        if value.strip().lower() == PALIMPSEST_SELF:
            raise ValueError(
                f"curate must not self-attribute: {label}={value!r} names "
                f"palimpsest itself (ADR-20260706 §결정5)"
            )


def _build_prompt(request: CurateRequest) -> str:
    if request.prompt is not None:
        return request.prompt
    refs = ", ".join(request.grounding_ids)
    return (
        f"Summarise {request.target_id} from these facts, grounding each claim "
        f"in one of [{refs}]; state what is NOT covered (gap) and a confidence.\n"
        f"Facts: {request.facts}"
    )


def produce(request: CurateRequest, *, generate: Callable[[str], str]) -> dict:
    """Produce exactly one summary payload for ``request``.

    ``generate`` is the LLM: it receives the prompt and returns a JSON object
    ``{"claims": [{"text", "source_refs"}], "gap": ..., "confidence": ...}``. The
    producer grounds every claim against the request's real candidate ids, drops
    hallucinated refs, and refuses a summary with no surviving grounded claim
    (never ungrounded prose). Returns a dict shaped to the Summary load schema.
    """
    _honest_provenance(request.generator, request.model)

    prompt = _build_prompt(request)
    generated = json.loads(generate(prompt))

    allowed = set(request.grounding_ids)
    claims = []
    for raw in generated.get("claims", []):
        refs = [r for r in raw.get("source_refs", []) if r in allowed]
        if not refs:
            continue  # drop a claim whose refs are all hallucinated/unknown
        claims.append({"text": raw["text"], "source_refs": refs})
    if not claims:
        raise ValueError(
            "curate produced no grounded claim (all refs outside the candidate "
            "set) — refusing to materialise ungrounded prose"
        )

    if generated.get("gap") is None:
        raise ValueError("curate payload missing gap (honesty form incomplete)")
    if generated.get("confidence") is None:
        raise ValueError("curate payload missing confidence (honesty form incomplete)")

    return {
        "target_id": request.target_id,
        "claims": claims,
        "generator": request.generator,
        "model": request.model,
        "source_commit": request.source_commit,
        "created_at": request.created_at,
        "prompt": prompt,
        "confidence": generated["confidence"],
        # The honesty form's third leg. It rides alongside the Summary schema; the
        # existing loader ignores unknown keys, so the payload loads unchanged.
        "gap": generated["gap"],
    }
