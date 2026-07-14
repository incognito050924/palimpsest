"""Grammar-agnostic JVM outbound-HTTP caller recognizer (wi_260713iah).

The JVM analogue of ``ecmascript.py:_scan_http_calls``, factored the way
``extract.spring`` centralizes Spring semantics for java.py + kotlin.py: this module
knows the HTTP-client CALL SHAPES (which method names carry a verb, which fluent
method carries the URL) but NOTHING about any parser. Each language extractor walks
its OWN tree into the normalized :class:`JvmHttpCall` records below, so both tiers
recognize a call identically — no per-grammar divergence (spring.py:4-6 norm).

Recognition keys off the callee's resolved import ORIGIN (Frozen Invariant 5,
``http_origins.is_recognized_call``) — the chain-root receiver's declared type is
resolved through the file's single-type imports to a Java FQN, and only a REGISTERED
construct (``WebClient`` / ``RestTemplate``) is recognized; an in-house wrapper
(``io.incognito.rest.client``) resolves to an unregistered origin and is a disclosed
gap. This node handles a LITERAL URL at the call site only — a non-literal
(variable / helper-passed) URL emits NOTHING here (the one-hop param->uri dataflow
is a separate downstream node).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from palimpsest.ir import Node, Provenance, API_CALL, ANY_METHOD, api_call_qualified_name
from palimpsest.extract.http_origins import is_recognized_call

# RestTemplate blocking-client verb methods -> HTTP verb; the URL is the FIRST arg.
# ``exchange``/``execute`` take the verb from a literal ``HttpMethod.X`` argument,
# falling back to the ANY_METHOD wildcard when it is not statically determinable
# (the ir/calls_api confidence ladder handles the wildcard at 0.4 — no invention).
_REST_TEMPLATE_METHODS = {
    "getForObject": "GET",
    "getForEntity": "GET",
    "postForObject": "POST",
    "postForEntity": "POST",
    "postForLocation": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patchForObject": "PATCH",
    "headForHeaders": "HEAD",
    "optionsForAllow": "OPTIONS",
    "exchange": ANY_METHOD,
    "execute": ANY_METHOD,
}

# WebClient fluent verb-selector methods (``webClient.get().uri(...)``).
_WEBCLIENT_VERBS = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "head": "HEAD",
    "options": "OPTIONS",
}
# The fluent URL-bearing method that carries the literal path in a WebClient chain.
_URI_METHOD = "uri"


@dataclass(frozen=True)
class JvmHttpCall:
    """One candidate outbound-HTTP call site, read grammar-agnostically.

    ``receiver_type`` — SIMPLE type name of the chain-root receiver (``restTemplate``
        typed ``RestTemplate`` -> ``"RestTemplate"``), or None when it cannot be typed.
    ``call_name``     — THIS invocation's method name (``getForObject`` / ``uri`` / ...).
    ``url_literal``   — the first string-literal argument, kept WITH its enclosing
        quotes (``api_call_qualified_name`` strips them), or None for a non-literal.
    ``chain_verbs``   — verb-selector method names appearing in the object chain
        leading to this call (WebClient's ``.get()`` sits here for a ``.uri(...)`` call).
    ``method_arg``    — a literal ``HttpMethod.X`` verb passed to ``exchange`` (``"POST"``),
        else None.
    ``base_url_field`` — the caller field NAME the URL is concatenated onto
        (``baseUrl + "/x"`` -> ``"baseUrl"``), when the URL is a ``<field> + literal``
        S2S base-url reference rather than a bare literal; else None (config grounding,
        wi_260713iah ac-5). The extractor sets it; ``base_url`` carries the resolution.
    ``base_url``      — the RESOLVED target host (``"http://dwp-b-portal-service:8080"``)
        the extractor grounded ``base_url_field`` to via ``spring_config``, or None when
        it could not be grounded (an honest gap — the call then emits NO ApiCall).
    """

    receiver_type: Optional[str]
    call_name: str
    url_literal: Optional[str]
    chain_verbs: tuple[str, ...]
    method_arg: Optional[str]
    start_line: int
    end_line: int
    base_url_field: Optional[str] = None
    base_url: Optional[str] = None


def http_method_of_arg(text: str) -> Optional[str]:
    """The verb of a literal ``HttpMethod.X`` argument (``"HttpMethod.POST"`` -> ``"POST"``),
    else None — the grammar-agnostic reader both extractors feed ``exchange``'s 2nd arg."""
    prefix = "HttpMethod."
    if text.startswith(prefix):
        verb = text[len(prefix):]
        return verb if verb.isalpha() else None
    return None


def webclient_verb(chain_verbs: tuple[str, ...]) -> Optional[str]:
    """The HTTP verb a WebClient chain's verb-selector implies (``.get()`` -> ``GET``),
    or None when the chain shows no recognized verb selector."""
    for name in chain_verbs:
        mapped = _WEBCLIENT_VERBS.get(name)
        if mapped:
            return mapped
    return None


def call_verb(call: JvmHttpCall) -> Optional[str]:
    """The HTTP verb a recognized URL-bearing call SHAPE implies, independent of whether
    the URL itself is literal (used by the one-hop dataflow recovery, which recognizes a
    helper's ``uri(...)`` / verb-method shape but sources the path from the CALL site).

    A RestTemplate verb method takes its verb from the method name (``exchange`` from a
    literal ``HttpMethod`` arg, else the ANY_METHOD wildcard). A WebClient ``.uri(...)``
    takes its verb from the chain's ``.get()`` / ``.post()`` selector (wildcard when the
    chain shows none). None when the call is not a recognized URL-bearing shape."""
    verb = _REST_TEMPLATE_METHODS.get(call.call_name)
    if verb is not None:
        if verb == ANY_METHOD and call.method_arg:
            return call.method_arg
        return verb
    if call.call_name == _URI_METHOD:
        return webclient_verb(call.chain_verbs) or ANY_METHOD
    return None


@dataclass(frozen=True)
class UriHelper:
    """A one-hop URL-forwarding helper (wi_260713iah, part 1).

    A private method whose parameter at ``param_index`` FLOWS, within its own body, into a
    recognized ``uri(...)`` / verb-method HTTP call carrying HTTP ``verb``. The concrete
    route is NOT in the helper — it is the LITERAL passed at the helper's call site, one
    hop away. The extractor pairs this with each same-file call site that passes a string
    literal at ``param_index`` to recover the ApiCall (strictly one hop; a variable /
    assembled argument stays an honest gap)."""

    name: str
    param_index: int
    verb: str


@dataclass(frozen=True)
class DataflowRecovery:
    """One recovered call: the ``verb`` from the helper's shape + the literal ``url`` from
    the call site, with the call-site line grounding. Fed to :func:`dataflow_api_call_nodes`."""

    verb: str
    url_literal: str
    start_line: int
    end_line: int


def dataflow_api_call_nodes(
    recoveries: list[DataflowRecovery], rel_path: str, prov: Provenance
) -> list[Node]:
    """Build the :data:`API_CALL` nodes for one-hop dataflow-recovered calls (part 1).

    Recognition already happened when the helper was identified (its client resolved to a
    REGISTERED origin), so — unlike :func:`api_call_nodes` — no receiver check is repeated
    here; the input is already-recognized recoveries. Each recovery's literal is templated
    by :func:`api_call_qualified_name` (a non-templatable literal -> no node, the ac-6 gap);
    same (method, path) dedupes to one node."""
    out: list[Node] = []
    seen: set[str] = set()
    for r in recoveries:
        qn = api_call_qualified_name(r.verb, r.url_literal)
        if qn is None or qn in seen:
            continue
        seen.add(qn)
        out.append(
            Node(
                kind=API_CALL,
                qualified_name=qn,
                name=r.verb,
                provenance=prov,
                path=rel_path,
                start_line=r.start_line,
                end_line=r.end_line,
                # ac-4: mark the node one-hop dataflow-recovered so the cross-tier
                # matcher (through ingest + loader) discounts the CALLS_API link below
                # the literal 1.0 tier — a recovered route is not a direct call-site
                # literal. The direct-literal scan (:func:`api_call_nodes`) leaves this
                # None, so a genuine literal match stays uncapped.
                dataflow_derived=True,
            )
        )
    return out


def _method_and_url(call: JvmHttpCall) -> Optional[tuple[str, str]]:
    """``(method, raw_url)`` for a recognized literal-URL call shape, else None.

    A RestTemplate verb method takes the URL from arg[0] and the verb from the method
    name (``exchange`` from a literal ``HttpMethod`` arg, else wildcard). A WebClient
    ``.uri(url)`` takes the URL from arg[0] and the verb from the chain's ``.get()`` /
    ``.post()`` selector (wildcard when the chain shows none)."""
    verb = _REST_TEMPLATE_METHODS.get(call.call_name)
    if verb is not None:
        if call.url_literal is None:
            return None
        if verb == ANY_METHOD and call.method_arg:
            verb = call.method_arg
        return verb, call.url_literal
    if call.call_name == _URI_METHOD and call.url_literal is not None:
        for name in call.chain_verbs:
            mapped = _WEBCLIENT_VERBS.get(name)
            if mapped:
                return mapped, call.url_literal
        return ANY_METHOD, call.url_literal
    return None


def _bind_target_host(qn: str, base_url: str) -> str:
    """Inject a grounded target host into an ``apicall:{method} {path}`` identity.

    ``apicall:GET /portal/api/user`` + ``http://dwp-b-portal-service:8080`` ->
    ``apicall:GET http://dwp-b-portal-service:8080/portal/api/user``. The host is kept
    verbatim (its ``://`` scheme is preserved, not path-normalized) so it binds WHICH
    service the call targets — a same-path call to a different host is a DISTINCT node,
    so grounding cannot conflate two services onto one ApiCall (ac-5). ``qn`` is
    ``apicall:{method} {path}``; the split is on the first space (after the method)."""
    prefix, _, path = qn.partition(" ")
    return f"{prefix} {base_url}{path}"


def api_call_nodes(
    calls: list[JvmHttpCall],
    import_map: dict[str, str],
    rel_path: str,
    prov: Provenance,
) -> list[Node]:
    """Every :data:`API_CALL` node the recognized literal-URL calls in one file emit.

    ``import_map`` maps a single-type import's SIMPLE name to its resolved Java FQN
    (``RestTemplate`` -> ``org.springframework.web.client.RestTemplate``). A call is
    recognized iff its chain-root receiver's type resolves to a REGISTERED origin
    (``is_recognized_call``); same (method, path) dedupes to one node."""
    out: list[Node] = []
    seen: set[str] = set()
    for call in calls:
        origin = import_map.get(call.receiver_type) if call.receiver_type else None
        if not is_recognized_call(call.receiver_type or "", origin):
            continue
        resolved = _method_and_url(call)
        if resolved is None:
            continue
        method, raw_url = resolved
        qn = api_call_qualified_name(method, raw_url)
        if qn is None:
            continue
        if call.base_url_field is not None:
            # A ``<field> + "/path"`` S2S base-url call: emit ONLY when config grounding
            # resolved the field's @Value to a target host (ac-5). An ungrounded base-url
            # is an honest gap — no node — never a bare-path ApiCall that could
            # coincidentally match an Endpoint in a different service.
            if call.base_url is None:
                continue
            qn = _bind_target_host(qn, call.base_url)
        if qn in seen:
            continue
        seen.add(qn)
        out.append(
            Node(
                kind=API_CALL,
                qualified_name=qn,
                name=method,
                provenance=prov,
                path=rel_path,
                start_line=call.start_line,
                end_line=call.end_line,
            )
        )
    return out
