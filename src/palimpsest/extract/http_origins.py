"""The explicit registry of recognized outbound-HTTP constructs (Decision 5).

This module is the §4-11 *code authority* for the one distinction that governs
front-end ApiCall recognition (ac-10): a **framework / library** HTTP construct
(``fetch``, ``axios``, ``node-fetch``) is a real outbound call we can match to a
back-end Endpoint, whereas a **project-local wrapper** (``import {api} from
'./client'``) is a disclosed gap — we do NOT descend into it, we stop at the
wrapper level (Frozen Invariant 5).

The registry is an EXPLICIT, EXTENSIBLE frozen tuple, not a heuristic: recognizing
a new HTTP library is a one-line addition here, keyed off the import ORIGIN, never
off call syntax. JS-family origins are the bare module name (``axios``); JVM-family
origins are the resolved Java FQN of the standard framework/library construct —
Spring ``WebClient`` / ``RestTemplate`` and OpenFeign ``@FeignClient``. Only
framework/standard constructs enter the registry; a project's own HTTP wrapper
(``io.incognito.rest.client``, ``./client``) stays an honest gap
(ADR-20260713-ontology-framework-constructs-not-project-patterns).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HttpConstruct:
    """One recognized HTTP construct.

    ``name``   — the construct's callee identifier (a global's name, or a package's
                 default-export name, e.g. ``axios``).
    ``kind``   — ``"global"`` (an ambient callee needing no import) or ``"package"``
                 (recognized only when the callee resolves to ``origin``).
    ``origin`` — ``""`` for a global; for a package the import specifier that binds
                 it — JS: the bare module name (``axios`` / ``node-fetch``); JVM: the
                 resolved Java FQN of the type/annotation
                 (``org.springframework.web.client.RestTemplate``). ``kind`` already
                 separates a JS-global (``fetch``) from a package/FQN origin, so no
                 per-ecosystem field is needed: recognition keys off this ``origin``.
    """

    name: str
    kind: str
    origin: str


# The extensible frozen registry. Recognition keys off ``kind``/``origin`` — adding
# a library is one tuple entry, never a change to the recognition rule below.
HTTP_CONSTRUCTS: tuple[HttpConstruct, ...] = (
    HttpConstruct(name="fetch", kind="global", origin=""),
    HttpConstruct(name="axios", kind="package", origin="axios"),
    HttpConstruct(name="node-fetch", kind="package", origin="node-fetch"),
    # JVM standard framework/library constructs — origin is the resolved Java FQN a
    # JVM scanner binds the callee to (ADR-20260713). Project-local wrappers
    # (io.incognito.rest.client) are absent by design: the honest gap.
    HttpConstruct(
        name="WebClient",
        kind="package",
        origin="org.springframework.web.reactive.function.client.WebClient",
    ),
    HttpConstruct(
        name="RestTemplate",
        kind="package",
        origin="org.springframework.web.client.RestTemplate",
    ),
    HttpConstruct(
        name="FeignClient",
        kind="package",
        origin="org.springframework.cloud.openfeign.FeignClient",
    ),
)

_GLOBAL_NAMES: frozenset[str] = frozenset(
    c.name for c in HTTP_CONSTRUCTS if c.kind == "global"
)
_PACKAGE_ORIGINS: frozenset[str] = frozenset(
    c.origin for c in HTTP_CONSTRUCTS if c.kind == "package"
)


def is_recognized_global(name: str) -> bool:
    """True when ``name`` is a registered ambient HTTP global (``fetch``)."""
    return name in _GLOBAL_NAMES


def recognizes_specifier(specifier: str) -> bool:
    """True when an import ``specifier`` binds a recognized HTTP package.

    JS exact-bare match: a relative specifier (``./x`` / ``../x``) is a project-local
    wrapper — the disclosed gap (Frozen Invariant 5) — and never matches; a bare
    specifier equal to a registered package origin (``axios`` / ``node-fetch``) does.
    """
    if specifier.startswith("./") or specifier.startswith("../"):
        return False
    return specifier in _PACKAGE_ORIGINS


def is_recognized_call(base: str, import_specifier: Optional[str]) -> bool:
    """THE recognition rule (Decision 5) — keys off the callee's resolved ORIGIN,
    not call syntax (Frozen Invariant 5).

    ``base`` is the callee identifier (``fetch`` / ``axios`` / a wrapper name);
    ``import_specifier`` is the raw specifier ``base`` was imported from, or ``None``
    when ``base`` has no import binding in the file.

      * no binding  -> recognized iff ``base`` is a registered global (``fetch``);
      * bare/pkg    -> recognized iff the specifier is a registered package origin;
      * relative    -> NOT recognized (a ``./client`` import is a project wrapper —
                       recognition stops at the wrapper level, the disclosed gap).
    """
    if import_specifier is None:
        return is_recognized_global(base)
    return recognizes_specifier(import_specifier)
