"""Live-Neo4j rig for reconcile / branch-scoped ingest (wi_260702y0d).

Same session-scoped testcontainers pattern as ``tests/backfill/conftest.py``
(one Neo4j 5 Community container, auto lifecycle) plus a per-test wiped DB. A
hermetic MULTI-BRANCH git repo fixture backs the N-way capture tests.
"""

import os
import subprocess
from pathlib import Path

import pytest

NEO4J_IMAGE = "neo4j:5-community"
NEO4J_PASSWORD = "palimpsest-test"

# Reuse the extraction fixture (a real Java file the extractor parses).
FIXTURES = Path(__file__).parents[1] / "extract" / "fixtures"
FIXTURE_JAVA = (
    FIXTURES
    / "src/main/java/kr/co/ecoletree/service/commute/service/CommuteService.java"
)

BASE_DATE = "2020-01-01T00:00:00 +0000"
MAIN_DATE = "2020-02-01T00:00:00 +0000"
FEAT_DATE = "2020-03-01T00:00:00 +0000"


def _configure_docker_endpoint() -> None:
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


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = None
    if date is not None:
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=env,
    ).stdout


@pytest.fixture(scope="session")
def neo4j_driver():
    from testcontainers.neo4j import Neo4jContainer

    with Neo4jContainer(NEO4J_IMAGE, password=NEO4J_PASSWORD) as container:
        driver = container.get_driver()
        try:
            driver.verify_connectivity()
            yield driver
        finally:
            driver.close()


@pytest.fixture
def db(neo4j_driver):
    """A wiped DB (function scope)."""
    with neo4j_driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    return neo4j_driver


@pytest.fixture
def multi_branch_repo(tmp_path):
    """A hermetic repo with a shared base then two divergent branches.

    ``main`` and ``feature`` share the base commit, then each adds a distinct
    method to the SAME class (same qualified_name, divergent content) — so the
    N-way capture must keep them as distinct branch-scoped nodes (ac-1) while the
    shared base tree is extracted once (dedup axis).
    """
    repo = tmp_path / "multirepo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "reconcile-test@example.com")
    _git(repo, "config", "user.name", "Reconcile Test")

    dst = repo / "CommuteService.java"
    original = FIXTURE_JAVA.read_text()
    dst.write_text(original)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base: add CommuteService", date=BASE_DATE)

    def _add_method(sig: str) -> None:
        head, _, tail = dst.read_text().rpartition("}")
        dst.write_text(head + f"\n\t{sig}\n}}" + tail)

    # main tip
    _add_method("public Map<String, Object> selectOnMain(Map<String, Object> p);")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "main: probe", date=MAIN_DATE)

    # feature branch from base
    base = _git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    _git(repo, "checkout", "-b", "feature", base)
    _add_method("public Map<String, Object> selectOnFeature(Map<String, Object> p);")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "feature: probe", date=FEAT_DATE)

    _git(repo, "checkout", "main")
    return repo
