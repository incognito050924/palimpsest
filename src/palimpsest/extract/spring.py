"""Grammar-agnostic Spring HTTP-API mapper (wi_260713c7t, design contract §Cross-cutting).

This module knows Spring's annotation semantics but NOTHING about any parser: it
consumes plain :class:`AnnotationInfo` records that a language extractor builds from
its own tree (java.py today, kotlin.py next), so both tiers map Spring identically —
no per-grammar divergence. Route identity/normalization lives in ``ir`` (imported
here); this file only decides METHODs, PATHs, stereotype ROLE and the emission gate.

Public API consumed by the extractors (kotlin.py imports the same surface):
  * ``AnnotationInfo(name, args, named_args)`` — one read annotation.
  * ``spring_role(class_anns) -> str | None`` — stereotype role, precedence
    controller > repository > service > component.
  * ``spring_endpoints(class_anns, method_anns) -> list[tuple[str, str]]`` — the
    ``(METHOD, endpoint_qualified_name)`` pairs a single handler method emits.
  * ``join_route(base, method_path) -> str`` — class-base ⋈ method-path (ir-normalized).
  * ``endpoint_qualified_name(method, path, discriminator="spring") -> str``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from palimpsest.ir import ANY_METHOD, normalize_endpoint_path


@dataclass(frozen=True)
class AnnotationInfo:
    """One annotation read off a class/method, reduced to what Spring mapping needs.

    ``name`` is the SIMPLE annotation name (``RestController``, ``GetMapping``, ...).
    ``args`` are POSITIONAL string-literal values (quotes stripped; non-literals and
    non-string args are dropped). ``named_args`` maps each ``key=value`` attribute to
    its value text (string literals unquoted; other value expressions kept verbatim,
    e.g. ``method`` -> ``"RequestMethod.POST"``).
    """

    name: str
    args: tuple[str, ...] = ()
    named_args: Mapping[str, str] = field(default_factory=dict)


# @<X>Mapping shorthand -> HTTP verb (design contract Decision 2e).
_MAPPING_METHOD = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}

# Spring stereotype annotation -> DI role marker (design contract Decision 6).
_STEREOTYPE_ROLE = {
    "RestController": "controller",
    "Controller": "controller",
    "Repository": "repository",
    "Service": "service",
    "Component": "component",
}

# Role precedence: the strongest stereotype a class carries wins.
_ROLE_PRECEDENCE = ("controller", "repository", "service", "component")

# `RequestMethod.POST` etc. inside a @RequestMapping(method=...) attribute.
_REQUEST_METHOD = re.compile(r"RequestMethod\.(\w+)")


def _names(anns) -> set[str]:
    return {a.name for a in anns}


def spring_role(class_anns) -> str | None:
    """The DI role a class plays, or ``None`` if it carries no Spring stereotype.

    Precedence controller > repository > service > component: a class annotated with
    several stereotypes resolves to the strongest one.
    """
    roles = {_STEREOTYPE_ROLE[a.name] for a in class_anns if a.name in _STEREOTYPE_ROLE}
    for role in _ROLE_PRECEDENCE:
        if role in roles:
            return role
    return None


def join_route(base: str, method_path: str) -> str:
    """Join a class-level base path with a method-level path (design contract 2c).

    ``base`` is the class ``@RequestMapping`` path literal (else ``""``); ``method_path``
    is the method mapping path literal (else ``""``). The result is
    ``normalize_endpoint_path(base + "/" + method_path)`` — empty + empty -> ``"/"``.
    """
    combined = (base or "") + "/" + (method_path or "")
    return normalize_endpoint_path(combined)


def endpoint_qualified_name(method: str, path: str, discriminator: str = "spring") -> str:
    """The tier-discriminated Endpoint identity, e.g. ``"spring:GET /api/orders"``.

    The ``discriminator:`` prefix (default ``"spring"``) namespaces the back-end plane
    away from the prefix-less SvelteKit front-end plane so same-route endpoints on the
    two tiers are DISTINCT nodes (design contract Decision 1).
    """
    return f"{discriminator}:{method} {normalize_endpoint_path(path)}"


def _mapping_methods(ann: AnnotationInfo) -> list[str]:
    """HTTP verbs an annotation maps to, or ``[]`` if it is not a mapping annotation.

    A ``@GetMapping``/... shorthand -> its fixed verb. A ``@RequestMapping`` reads its
    ``method=`` attribute (``RequestMethod.X`` -> ``X``, possibly several); with no
    ``method=`` it answers any verb -> the ``ANY_METHOD`` sentinel.
    """
    if ann.name in _MAPPING_METHOD:
        return [_MAPPING_METHOD[ann.name]]
    if ann.name == "RequestMapping":
        raw = ann.named_args.get("method")
        if raw:
            verbs = _REQUEST_METHOD.findall(raw)
            if verbs:
                return verbs
        return [ANY_METHOD]
    return []


def _mapping_paths(ann: AnnotationInfo) -> list[str]:
    """The path literal(s) an annotation declares.

    A positional literal (``@GetMapping("/x")`` or an array ``{"/a","/b"}``) wins;
    else the ``value``/``path`` attribute. An empty list means the mapping declares no
    path of its own and maps onto the class base alone.
    """
    if ann.args:
        return list(ann.args)
    for key in ("value", "path"):
        val = ann.named_args.get(key)
        if val is not None:
            return [val]
    return []


def _base_path(class_anns) -> str:
    """The class-level ``@RequestMapping`` base path, or ``""`` if none."""
    for ann in class_anns:
        if ann.name == "RequestMapping":
            paths = _mapping_paths(ann)
            if paths:
                return paths[0]
    return ""


def _emits_body(class_anns, method_anns) -> bool:
    """Whether a handler method returns an HTTP body (an API Endpoint) vs a view.

    A ``@RestController`` implies ``@ResponseBody`` on every method. A plain
    ``@Controller`` method is an API Endpoint only when it (or its class) carries
    ``@ResponseBody`` — a view-returning ``@Controller`` method is NOT an Endpoint
    (design contract Decision "honest exclusion").
    """
    cls = _names(class_anns)
    if "RestController" in cls:
        return True
    return "ResponseBody" in cls or "ResponseBody" in _names(method_anns)


def spring_endpoints(class_anns, method_anns) -> list[tuple[str, str]]:
    """The ``(METHOD, endpoint_qualified_name)`` pairs one handler method emits.

    Returns ``[]`` unless the class is a controller (``@RestController`` /
    ``@Controller``) AND the method emits a body (see :func:`_emits_body`). For each
    mapping annotation on the method, the result is the cartesian product of its verbs
    and its paths (each joined onto the class base), deduplicated in first-seen order.
    """
    cls = _names(class_anns)
    if "RestController" not in cls and "Controller" not in cls:
        return []
    if not _emits_body(class_anns, method_anns):
        return []

    base = _base_path(class_anns)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for ann in method_anns:
        verbs = _mapping_methods(ann)
        if not verbs:
            continue
        paths = _mapping_paths(ann) or [""]
        for verb in verbs:
            for path in paths:
                qn = endpoint_qualified_name(verb, join_route(base, path))
                if qn not in seen:
                    seen.add(qn)
                    out.append((verb, qn))
    return out
