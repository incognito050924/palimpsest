"""TDD for ECMAScript outbound-HTTP ApiCall recognition (wi_260713c7t, n-ecma-origin-recognizer).

Hermetic inline corpus under ``tmp_path`` (same style as the JS/TS extractor tests).
These pin the origin-keyed recognizer (Decision 5) + ApiCall emission (Decision 3):

  ac-3 (positive): a recognized construct — the ``fetch`` GLOBAL, or a call whose
     callee resolves via IMPORTS to a registry package (``axios``) — becomes an
     :data:`API_CALL` node whose identity is ``apicall:{METHOD} {template}`` with the
     URL's dynamic parts collapsed to ``{}`` (``api_call_qualified_name``).
  ac-4 (negative, the PRINCIPLE): a callee resolving to a PROJECT-LOCAL import
     (``./client``) is NOT recognized — the wrapper level is a disclosed gap
     (Frozen Invariant 5). Recognition keys off the resolved import ORIGIN, not the
     ``x.get(...)`` call syntax. But a raw ``fetch(...)`` INSIDE that local module IS
     recognized (the gap is the wrapper, not its innards).
  ac-6 (gap): a dynamic URL (bare variable / runtime concat with no static segment)
     emits NOTHING — an honest absence, never a false ApiCall.
"""

from pathlib import Path

from palimpsest.ir import Provenance, API_CALL
from palimpsest.extract.javascript import extract as extract_js
from palimpsest.extract.typescript import extract as extract_ts

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)


def _extract(extract_fn, tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract_fn(tmp_path, PROV, repo_name="R")


def _apicall_qns(ir):
    return {n.qualified_name for n in ir.nodes_of(API_CALL)}


# --- ac-3 positive --------------------------------------------------------------

def test_fetch_global_string_url_is_apicall(tmp_path):
    # The `fetch` GLOBAL with a static string URL and no method option -> GET.
    ir = _extract(extract_js, tmp_path, {"orders.js": "fetch('/api/orders');\n"})
    assert "apicall:GET /api/orders" in _apicall_qns(ir)


def test_fetch_method_option_extracted(tmp_path):
    # The method is read from the fetch options object literal.
    ir = _extract(
        extract_js, tmp_path,
        {"create.js": "fetch('/api/orders', { method: 'POST' });\n"},
    )
    assert "apicall:POST /api/orders" in _apicall_qns(ir)


def test_axios_import_verb_and_concat_url(tmp_path):
    # Callee `axios` resolves via IMPORTS to the registry package 'axios' (recognized);
    # `.get` -> GET; `'/api/users/' + id` runtime concat KEEPS its static prefix and
    # collapses the dynamic tail -> apicall:GET /api/users/{}.
    src = "import axios from 'axios';\naxios.get('/api/users/' + id);\n"
    ir = _extract(extract_ts, tmp_path, {"users.ts": src})
    assert "apicall:GET /api/users/{}" in _apicall_qns(ir)


def test_axios_template_literal_url(tmp_path):
    # A template literal `/api/users/${id}` -> same normalized template.
    src = "import axios from 'axios';\naxios.get(`/api/users/${id}`);\n"
    ir = _extract(extract_ts, tmp_path, {"users2.ts": src})
    assert "apicall:GET /api/users/{}" in _apicall_qns(ir)


# --- ac-4 negative: the framework-vs-project-wrapper principle -------------------

def test_project_local_wrapper_call_is_not_apicall(tmp_path):
    # `api` resolves via IMPORTS to a RELATIVE './client' -> project wrapper -> NOT
    # recognized. The `api.get('/wrapped')` call MUST emit no ApiCall...
    files = {
        "consumer.ts": "import { api } from './client';\napi.get('/wrapped');\n",
        # ...but a raw `fetch(...)` INSIDE the local module IS recognized (the gap is
        # the wrapper level, not the fetch inside it).
        "client.ts": "export const api = { get: (u) => fetch('/api/inner/' + u) };\n",
    }
    ir = _extract(extract_ts, tmp_path, files)
    qns = _apicall_qns(ir)
    assert "apicall:GET /wrapped" not in qns          # wrapper call -> disclosed gap
    assert "apicall:GET /api/inner/{}" in qns          # raw fetch inside IS recognized


# --- ac-6: dynamic URL is a disclosed gap ---------------------------------------

def test_dynamic_url_emits_no_apicall(tmp_path):
    # A bare variable and a literal-free runtime concat are non-templatable -> no node.
    files = {
        "dyn.js": "fetch(someVar);\nfetch(base + path);\n",
    }
    ir = _extract(extract_js, tmp_path, files)
    assert _apicall_qns(ir) == set()
