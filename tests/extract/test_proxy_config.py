"""TDD for the host-neutral dev-proxy config reader (wi_260713iah, mechanism A).

WHY this file exists (background for approver + consumer):

Cross-tier CALLS_API matching needs to know how a Vite/Svelte dev-proxy maps the
front-end ``/api`` prefix to the back-end. ``read_proxy_rewrite`` reads that mapping
from the REPO'S OWN config with tree-sitter — it must NEVER hardcode one repo's
``/api``->target rule (host-neutral), and it must DEGRADE a rule it cannot evaluate
statically (a function-valued ``rewrite``, an env-only ``target``) to an honest GAP
that SUPPRESSES the match rather than guess. These tests pin:

  * KEEP: an object entry with a literal (or literal-fallback) target and NO
    ``rewrite`` key -> prefix forwarded unchanged (boxwood automation shape).
  * GAP: a function-valued ``rewrite`` (boxwood bpmn shape) OR an env-only target
    -> resolve() returns None so the caller drops the match (no false link).
  * host-neutral: a DIFFERENT prefix reads just as well; the reader carries no
    ``/api`` constant.
"""

from pathlib import Path

from palimpsest.extract.proxy_config import (
    GAP,
    KEEP,
    ProxyRewrite,
    ProxyRule,
    read_proxy_rewrite,
)


def _write(tmp_path: Path, rel: str, src: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src)
    return tmp_path


def _rule(rw: ProxyRewrite, prefix: str) -> ProxyRule:
    for r in rw.rules:
        if r.prefix == prefix:
            return r
    raise AssertionError(f"no rule for {prefix!r} in {rw.rules}")


# --- KEEP: no rewrite key, prefix forwarded unchanged -------------------------

def test_object_target_no_rewrite_is_keep(tmp_path):
    # boxwood automation shape: object target, changeOrigin, NO rewrite -> KEEP.
    root = _write(
        tmp_path, "vite.config.ts",
        "export default defineConfig({\n"
        "  server: { proxy: { '/api': { target: 'http://localhost:8080',"
        " changeOrigin: true } } }\n"
        "});\n",
    )
    rw = read_proxy_rewrite(root)
    r = _rule(rw, "/api")
    assert r.kind == KEEP
    assert r.target == "http://localhost:8080"
    # KEEP -> the match-level path is forwarded unchanged.
    assert rw.resolve("/api/orders/{}") == "/api/orders/{}"


def test_string_shorthand_target_is_keep(tmp_path):
    root = _write(
        tmp_path, "vite.config.js",
        "export default { server: { proxy: { '/api': 'http://localhost:8080' } } };\n",
    )
    r = _rule(read_proxy_rewrite(root), "/api")
    assert r.kind == KEEP
    assert r.target == "http://localhost:8080"


def test_env_target_with_literal_fallback_is_keep(tmp_path):
    # `env.X || 'literal'` — the literal fallback is readable -> KEEP (not a gap).
    root = _write(
        tmp_path, "vite.config.ts",
        "export default defineConfig(({ mode }) => ({\n"
        "  server: { proxy: { '/api': { target:"
        " env.VITE_API_SERVER_URL || 'http://localhost:8080', changeOrigin: true } } }\n"
        "}));\n",
    )
    r = _rule(read_proxy_rewrite(root), "/api")
    assert r.kind == KEEP
    assert r.target == "http://localhost:8080"


# --- GAP: unevaluable rewrite / target, match suppressed ----------------------

def test_function_rewrite_is_gap(tmp_path):
    # boxwood bpmn shape: `rewrite: (path)=>path.replace(...)` is a JS function
    # tree-sitter cannot evaluate -> GAP -> resolve() suppresses the match (None).
    root = _write(
        tmp_path, "vite.config.js",
        "export default { server: { proxy: { '/api': {\n"
        "  target: 'http://localhost:8080',\n"
        "  rewrite: (path) => path.replace(/^\\/api/, '')\n"
        "} } } };\n",
    )
    r = _rule(read_proxy_rewrite(root), "/api")
    assert r.kind == GAP
    assert rw_resolve_is_gap(read_proxy_rewrite(root), "/api/orders/{}")


def test_env_only_target_is_gap(tmp_path):
    # `target: process.env.X` with no literal fallback is unresolvable -> GAP.
    root = _write(
        tmp_path, "vite.config.ts",
        "export default defineConfig({\n"
        "  server: { proxy: { '/api': { target: process.env.API_URL } } }\n"
        "});\n",
    )
    r = _rule(read_proxy_rewrite(root), "/api")
    assert r.kind == GAP
    assert read_proxy_rewrite(root).resolve("/api/x") is None


def rw_resolve_is_gap(rw: ProxyRewrite, path: str) -> bool:
    return rw.resolve(path) is None


# --- host-neutral: a DIFFERENT prefix, no /api constant -----------------------

def test_host_neutral_non_api_prefix(tmp_path):
    # A repo whose proxy prefix is NOT /api must read just as well; and a /api path
    # (not declared) is NOT treated as proxied (resolves to itself).
    root = _write(
        tmp_path, "vite.config.ts",
        "export default defineConfig({\n"
        "  server: { proxy: { '/backend': { target: 'http://svc:9000' } } }\n"
        "});\n",
    )
    rw = read_proxy_rewrite(root)
    assert _rule(rw, "/backend").kind == KEEP
    assert rw.resolve("/backend/users/{}") == "/backend/users/{}"
    # /api is NOT a declared prefix here -> untouched (host-neutral, no /api bias).
    assert rw.resolve("/api/users/{}") == "/api/users/{}"


# --- no config / non-proxied path --------------------------------------------

def test_no_config_yields_empty_mapping(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export const x = 1;\n")
    rw = read_proxy_rewrite(tmp_path)
    assert rw.rules == ()
    assert rw.resolve("/api/x") == "/api/x"  # identity when nothing is proxied
