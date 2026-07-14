"""Config base-url grounding for JVM server-to-server callers (wi_260713iah, ac-5).

A JVM S2S caller almost never hard-codes its target host; it injects one via
``@Value("${boxwood.portal.base-url}")`` and concatenates a path onto it
(``restTemplate.getForObject(baseUrl + "/portal/api/x", ...)``). Without resolving
that property, the call's URL is a runtime concat — a disclosed gap (no ApiCall) —
OR, if we naively dropped the base and kept only ``/portal/api/x``, it could
coincidentally match an Endpoint in a DIFFERENT service and forge a false link.

This module GROUNDS the base-url: it reads the caller module's own
``application.yaml`` / ``application-<profile>.yaml`` and resolves the referenced
property KEY to its literal value, so the emitted ApiCall carries the target host
(binding which service) instead of a bare, ambiguous path (ac-5). When the property
cannot be resolved to a single literal host, this returns an honest GAP (``None``) —
NEVER a guessed binding — so the downstream matcher gets no false-positive edge
(ac-6 invariant).

Hard constraints (coverage sweep):
  * ``yaml.safe_load`` ONLY — never ``yaml.load``/the full loader. A config file can
    carry ``!!python/...`` tags that the full loader would CONSTRUCT (code execution);
    ``safe_load`` refuses them. A file that fails to parse safely is treated as absent.
  * Bind the referenced KEY only. This never returns, logs, or leaks the whole
    property map — only the single resolved scalar (secret-exposure constraint).
  * Classify the config shape BEFORE binding: a missing key with no literal default,
    a non-string/list value, an env-only ``${ENV:...}`` value, or a multi-profile
    disagreement all resolve to a GAP, never to a fabricated value.
  * host-neutral: reads the TARGET repo's own config; no host values are hardcoded.

Out of scope (absorbed by ac-6 as honest gaps): mapping a compose alias
(``http://engine:8088``) to a repo module (no topology resolver here), and resolving
an OS environment variable (env-only values stay gaps).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping, Optional

import yaml

# A Spring property placeholder: ``${key}`` or ``${key:default}``. The key never
# contains ``:`` or ``}``; the (optional) default is everything up to the final ``}``
# and MAY contain ``:`` (a ``http://...`` literal fallback). ``#{...}`` SpEL and any
# non-``${...}`` text are NOT this shape -> unresolvable (gap).
_VALUE_REF = re.compile(r"^\$\{([^:}]+)(?::(.*))?\}$")


def parse_value_ref(text: str) -> Optional[tuple[str, Optional[str]]]:
    """Parse a Spring ``@Value`` placeholder into ``(property_key, literal_default)``.

    ``text`` is the annotation's inner string (already unquoted by the extractor),
    e.g. ``"${boxwood.portal.base-url:http://localhost:8080}"``. Returns
    ``("boxwood.portal.base-url", "http://localhost:8080")``; a ref with no default
    yields ``(key, None)``. Returns ``None`` for anything that is not a single
    ``${...}`` property placeholder (a SpEL ``#{...}`` expression, a plain literal,
    or a composite string) — an unresolvable @Value shape, i.e. a gap.
    """
    m = _VALUE_REF.match(text.strip())
    if m is None:
        return None
    return m.group(1), m.group(2)


def _is_env_placeholder(value: str) -> bool:
    """True if a yaml value is itself a ``${...}`` placeholder (env-only indirection).

    The docker profile's ``base-url: ${BOXWOOD_PORTAL_BASE_URL:http://backend:8080}``
    resolves at runtime from an OS env var; its default host is a compose alias. Both
    env resolution and compose-alias->module mapping are out of scope, so an env-only
    value is a gap here — never resolved to its inner default.
    """
    return value.strip().startswith("${")


def _looks_like_base_url(value: str) -> bool:
    """A literal we accept as a resolvable base-url: it carries a scheme (``://``).

    This rejects a bare non-URL scalar (``"true"``, a number-as-string, an absent
    base-url that resolved to some unrelated token) so grounding never binds garbage.
    """
    return "://" in value


def _lookup(mapping: Mapping, dotted_key: str):
    """The scalar leaf a dotted property key addresses, or ``None``.

    Walks nested maps (``boxwood: {portal: {base-url: X}}``) and, failing that, tries
    the whole dotted key as a single flat map key (Spring accepts both spellings).
    Returns ONLY the addressed leaf — never a sub-map — so no sibling property is
    ever surfaced (key-only binding).
    """
    node = mapping
    for part in dotted_key.split("."):
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        else:
            node = None
            break
    if node is not None and not isinstance(node, (Mapping, list)):
        return node
    # flat "a.b.c:" key fallback
    if isinstance(mapping, Mapping) and dotted_key in mapping:
        flat = mapping[dotted_key]
        if not isinstance(flat, (Mapping, list)):
            return flat
    return None


def resolve_property(
    profile_maps: Mapping[Optional[str], Mapping],
    key: str,
    default: Optional[str],
) -> Optional[str]:
    """Resolve ONE property key across a module's profiles to a single literal host.

    ``profile_maps`` maps each profile name (``None`` = the base ``application.yaml``)
    to its parsed map. The rule, applied AFTER shape-classification:

      * Collect the key's LITERAL base-url values across every profile — a value that
        is a list, a non-string scalar, an env-only ``${...}`` placeholder, or not a
        scheme-bearing URL is NOT a literal and is skipped (its own gap).
      * Exactly one distinct literal -> resolve to it.
      * Two or more distinct literals -> AMBIGUOUS multi-profile disagreement -> GAP
        (``None``); we do NOT silently pick one (the confidence-cap is the dataflow
        node's job — here we simply refuse to fabricate a single authoritative value).
      * Zero literals -> fall back to the @Value ``:default`` if it is itself a literal
        scheme-bearing URL (``${key:http://localhost:8080}``), else GAP.

    Returns only the single resolved scalar — never the profile maps.
    """
    literals = set()
    for prof_map in profile_maps.values():
        value = _lookup(prof_map, key)
        if not isinstance(value, str):
            continue  # missing / list / non-string scalar -> not a literal
        if _is_env_placeholder(value) or not _looks_like_base_url(value):
            continue  # env-only indirection / absent-or-non-URL -> not a literal
        literals.add(value)

    if len(literals) == 1:
        return next(iter(literals))
    if len(literals) >= 2:
        return None  # multi-profile disagreement -> honest gap, no guess

    if default is not None and not _is_env_placeholder(default) and _looks_like_base_url(default):
        return default
    return None


def find_resources_dir(source_file: Path) -> Optional[Path]:
    """The ``src/main/resources`` dir of the module owning ``source_file``, or ``None``.

    Walks up from the caller's ``.java``/``.kt`` file (``.../<module>/src/main/java/
    .../X.java``) to the nearest ancestor holding a ``src/main/resources`` dir — the
    module's own config root. host-neutral: only the target repo's layout is read.
    """
    for parent in source_file.resolve().parents:
        candidate = parent / "src" / "main" / "resources"
        if candidate.is_dir():
            return candidate
    return None


def _safe_load_map(path: Path) -> Mapping:
    """``yaml.safe_load`` one file into a map, or ``{}`` on any parse failure.

    safe_load (NEVER the full loader) refuses ``!!python/...`` tags by raising rather
    than constructing an object, so a hostile/malformed config degrades to an empty
    map (a gap) instead of executing code or crashing extraction.
    """
    try:
        loaded = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return {}
    return loaded if isinstance(loaded, Mapping) else {}


def load_profile_maps(resources_dir: Path) -> dict[Optional[str], Mapping]:
    """Parse ``application.yaml`` + every ``application-<profile>.yaml`` under a dir.

    Keys the base file to ``None`` and each ``application-<profile>.yaml`` to its
    ``<profile>``. ``.yaml`` and ``.yml`` are both accepted. Each file is read with
    ``safe_load`` only (see :func:`_safe_load_map`).
    """
    out: dict[Optional[str], Mapping] = {}
    for ext in ("yaml", "yml"):
        base = resources_dir / f"application.{ext}"
        if base.is_file():
            out[None] = _safe_load_map(base)
        for path in sorted(resources_dir.glob(f"application-*.{ext}")):
            profile = path.stem[len("application-"):]
            out[profile] = _safe_load_map(path)
    return out


def resolve_base_url(source_file: Path, value_ref: str) -> Optional[str]:
    """Resolve a caller's ``@Value`` base-url reference to its target host, or GAP.

    ``source_file`` is the caller's absolute source path (used to locate the module's
    ``application*.yaml``); ``value_ref`` is the ``@Value`` inner string
    (``"${key:default}"``). Returns the resolved scheme-bearing base-url
    (``"http://dwp-b-portal-service:8080"``) when the config grounds it to a single
    literal, else ``None`` — an honest gap that must NOT become a downstream edge.
    """
    parsed = parse_value_ref(value_ref)
    if parsed is None:
        return None
    key, default = parsed
    resources = find_resources_dir(source_file)
    profile_maps = load_profile_maps(resources) if resources is not None else {}
    return resolve_property(profile_maps, key, default)
