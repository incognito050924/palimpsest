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
import re
from dataclasses import dataclass
from typing import Callable

# The one identifier the producer must never claim as generator/model: itself.
# curate synthesises with an EXTERNAL model and records that model honestly; it
# never attributes the generation to palimpsest (ADR-20260706 §결정5).
PALIMPSEST_SELF = "palimpsest"

# Verdict / recommendation language that breaks the synthesis-only framing (F-Q6):
# a candidate is a grounded co-occurrence OBSERVATION, never a quality judgment or a
# refactor recommendation. A generated claim carrying any of these is refused so no
# laundered verdict reaches git-SoT. Word-boundary matched (case-insensitive) so
# neutral structural terms (e.g. "coupled", "calls") are never over-rejected.
_VERDICT_LANGUAGE = re.compile(
    r"\b("
    r"should|must|ought|need(?:s)?\s+to|"
    r"refactor(?:ed|ing)?|recommend(?:ed|ation)?|extract\s+the|"
    r"violation|anti-?pattern|code\s+smell|smell(?:s|y)?|"
    r"poorly|badly|too\s+many|excessive|bloated|"
    r"improve|fix\b|worse|better\s+to"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CurateRequest:
    """One curate unit: what to summarise, and the REAL ids it may ground in.

    ``grounding_ids`` is the closed set of real graph-node ids (the target plus
    its structural neighbours) a claim is allowed to cite. The producer owns
    grounding — a claim ref outside this set is a hallucinated id and is dropped,
    so the loader never has to reject laundered refs after the fact.

    There is deliberately NO free-form ``prompt`` field: the producer always builds
    its own neutral synthesis prompt, so this flow cannot be steered with a
    judgment-style prompt (F-Q6 — the LLM synthesises an observation, it never
    selects candidates nor is asked to judge quality).
    """

    target_id: str
    grounding_ids: tuple[str, ...]
    facts: str
    source_commit: str
    created_at: str
    generator: str
    model: str


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
    """The producer's OWN neutral synthesis prompt (no override): describe what the
    facts show, grounded in the real ids — never recommend changes or judge quality
    (F-Q6 synthesis-only framing)."""
    refs = ", ".join(request.grounding_ids)
    return (
        f"Describe {request.target_id} from these facts, grounding each claim "
        f"in one of [{refs}]. Report only what the facts show as neutral "
        f"observations; do NOT recommend changes, judge quality, or refactor. "
        f"State what is NOT covered (gap) and a confidence.\n"
        f"Facts: {request.facts}"
    )


def _reject_verdict_language(claims: list) -> None:
    """Refuse the payload if any claim carries verdict/recommendation language — the
    synthesis-only framing (F-Q6) must hold on the OUTPUT, not just the prompt."""
    for claim in claims:
        m = _VERDICT_LANGUAGE.search(claim["text"])
        if m:
            raise ValueError(
                f"curate claim carries verdict/recommendation language "
                f"({m.group(0)!r}) — a candidate is a grounded observation, not a "
                f"judgment (F-Q6): {claim['text']!r}"
            )


def _validate_honesty_form(gap, confidence) -> None:
    """The honesty form's gap+confidence must be present AND well-formed: a non-empty
    gap and a numeric confidence in [0, 1] (a model score, deterministically bounded)."""
    if gap is None or not str(gap).strip():
        raise ValueError("curate payload missing/empty gap (honesty form incomplete)")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError(f"curate confidence must be a number in [0,1], got {confidence!r}")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"curate confidence out of range [0,1]: {confidence!r}")


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

    # F-Q6 synthesis-only framing: refuse verdict/recommendation language on the
    # OUTPUT, and validate the honesty form (non-empty gap, confidence in [0,1]).
    _reject_verdict_language(claims)
    _validate_honesty_form(generated.get("gap"), generated.get("confidence"))

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
