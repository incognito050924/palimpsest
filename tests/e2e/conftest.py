"""Live-Neo4j e2e rig for the palimpsest CLI (n5-impl-cli-e2e).

Same hermetic testcontainers pattern as ``tests/kg/conftest.py`` and
``tests/recall/conftest.py``: one Neo4j 5 Community container per session
(auto lifecycle). The difference here is that the CLI builds its *own* driver
from ``NEO4J_*`` env, so the per-test fixture points that env at the live
container and wipes the DB first — the whole extract -> ingest -> recall slice
is then exercised through the real CLI entry.
"""

import os
import subprocess
from pathlib import Path

import pytest


def _configure_docker_endpoint() -> None:
    """Point the docker SDK (testcontainers) at the active daemon.

    Docker Desktop on macOS does not expose the default ``/var/run/docker.sock``;
    the live endpoint lives in the docker CLI context. When ``DOCKER_HOST`` is
    unset, resolve it from ``docker context inspect`` so the hermetic rig can
    reach the daemon the CLI already talks to. The reaper (ryuk) is disabled
    because the container lifecycle is owned by the session fixture below.
    """
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    if os.environ.get("DOCKER_HOST"):
        return
    try:
        host = subprocess.run(
            ["docker", "context", "inspect", "-f",
             "{{.Endpoints.docker.Host}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return
    if host:
        os.environ["DOCKER_HOST"] = host


_configure_docker_endpoint()

# The n2 `commute` extraction fixtures: a small, real Java tree inside this git
# repo (so `read_provenance` resolves a real commit for the CLI ingest path).
FIXTURES = Path(__file__).parents[1] / "extract" / "fixtures"

NEO4J_IMAGE = "neo4j:5-community"
NEO4J_PASSWORD = "palimpsest-test"


@pytest.fixture(scope="session")
def neo4j_container():
    """A live Neo4j 5 Community container, session-scoped (auto lifecycle)."""
    from testcontainers.neo4j import Neo4jContainer

    with Neo4jContainer(NEO4J_IMAGE, password=NEO4J_PASSWORD) as container:
        driver = container.get_driver()
        try:
            driver.verify_connectivity()
        finally:
            driver.close()
        yield container


@pytest.fixture
def cli_env(neo4j_container, monkeypatch):
    """Wipe the DB and point the CLI's ``NEO4J_*`` env at the live container."""
    driver = neo4j_container.get_driver()
    try:
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
    finally:
        driver.close()
    monkeypatch.setenv("NEO4J_URI", neo4j_container.get_connection_url())
    monkeypatch.setenv("NEO4J_USER", neo4j_container.username)
    monkeypatch.setenv("NEO4J_PASSWORD", neo4j_container.password)
    return neo4j_container


# --- multi-branch git repo for the reconcile CLI surface (mirrors -------------
# tests/reconcile/conftest.py::multi_branch_repo; used by test_reconcile_e2e).
FIXTURE_JAVA = (
    FIXTURES
    / "src/main/java/kr/co/ecoletree/service/commute/service/CommuteService.java"
)
RECONCILE_AUTHOR_EMAIL = "reconcile-test@example.com"
# main's tip is the NEWEST instant, so `main` is the freshest peer (the reconcile
# e2e asserts main carries the `freshest` marker; no privileged branch otherwise).
_BASE_DATE = "2020-01-01T00:00:00 +0000"
_FEAT_DATE = "2020-02-01T00:00:00 +0000"
_MAIN_DATE = "2020-03-01T00:00:00 +0000"


def _git_at(repo: Path, *args: str, date: str | None = None) -> str:
    env = None
    if date is not None:
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=env,
    ).stdout


@pytest.fixture
def multi_branch_repo(tmp_path):
    """A hermetic repo: a shared base then two divergent branches (``main`` newer
    at a later date than ``feature``'s tip is older — freshness diverges). Same
    Class qualified_name on both branches, each with a branch-unique method."""
    repo = tmp_path / "multirepo"
    repo.mkdir()
    _git_at(repo, "init", "-b", "main")
    _git_at(repo, "config", "user.email", RECONCILE_AUTHOR_EMAIL)
    _git_at(repo, "config", "user.name", "Reconcile Test")

    dst = repo / "CommuteService.java"
    dst.write_text(FIXTURE_JAVA.read_text())
    _git_at(repo, "add", "-A")
    _git_at(repo, "commit", "-m", "base: add CommuteService", date=_BASE_DATE)

    def _add_method(sig: str) -> None:
        head, _, tail = dst.read_text().rpartition("}")
        dst.write_text(head + f"\n\t{sig}\n}}" + tail)

    # feature branch from base (older tip)
    base = _git_at(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    _git_at(repo, "checkout", "-b", "feature", base)
    _add_method("public Map<String, Object> selectOnFeature(Map<String, Object> p);")
    _git_at(repo, "add", "-A")
    _git_at(repo, "commit", "-m", "feature: probe", date=_FEAT_DATE)

    # main tip (newer)
    _git_at(repo, "checkout", "main")
    _add_method("public Map<String, Object> selectOnMain(Map<String, Object> p);")
    _git_at(repo, "add", "-A")
    _git_at(repo, "commit", "-m", "main: probe", date=_MAIN_DATE)
    return repo
