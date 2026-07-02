"""Live-Neo4j + hermetic-git rig for backfill (wi_260702asn).

Same session-scoped testcontainers pattern as ``tests/kg/conftest.py`` (one
Neo4j 5 Community container, auto lifecycle). Adds a HERMETIC fixture git repo
built in a tmpdir with two commits of a real fixture Java file, so backfill can
replay a genuine multi-commit history. Commit dates are pinned (and distinct) so
the "newest commit wins" freshness assertion is meaningful, not accidental.
"""

import os
import subprocess
from pathlib import Path

import pytest

# Reuse the n2 extraction fixtures (the `commute` slice) — a real Java file the
# extractor actually parses (only *.java is extracted).
FIXTURES = Path(__file__).parents[1] / "extract" / "fixtures"
FIXTURE_JAVA = (
    FIXTURES
    / "src/main/java/kr/co/ecoletree/service/commute/service/CommuteService.java"
)

# Two pinned, DISTINCT commit times so oldest->newest ordering is testable.
COMMIT1_DATE = "2020-01-01T00:00:00 +0000"
COMMIT2_DATE = "2021-06-15T12:30:00 +0000"

NEO4J_IMAGE = "neo4j:5-community"
NEO4J_PASSWORD = "palimpsest-test"


def _configure_docker_endpoint() -> None:
    """Point the docker SDK (testcontainers) at the active daemon.

    Docker Desktop on macOS does not expose the default ``/var/run/docker.sock``;
    the live endpoint lives in the docker CLI context. When ``DOCKER_HOST`` is
    unset, resolve it from ``docker context inspect``. The reaper (ryuk) is
    disabled because the container lifecycle is owned by the session fixture.
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


def _git(repo: Path, *args: str, date: str | None = None) -> None:
    env = None
    if date is not None:
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=env,
    )


@pytest.fixture(scope="session")
def neo4j_driver():
    """A live Neo4j driver backed by a session-scoped testcontainer."""
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
    """A wiped DB (function scope). Backfill provisions its own constraints."""
    with neo4j_driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    return neo4j_driver


@pytest.fixture
def repo(tmp_path):
    """A hermetic 2-commit git repo of a real fixture Java file.

    Commit 1 adds the file as-is. Commit 2 modifies it (adds a method to the
    interface) so the ``CommuteService`` Class node's content changes across the
    two commits while its identity (qualified_name) is preserved.
    """
    repo = tmp_path / "commuterepo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "backfill-test@example.com")
    _git(repo, "config", "user.name", "Backfill Test")

    dst = repo / "CommuteService.java"
    original = FIXTURE_JAVA.read_text()
    dst.write_text(original)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "commit 1: add CommuteService", date=COMMIT1_DATE)

    # Insert a new method inside the interface (before its closing brace) — a real
    # content change that keeps the Class node's identity.
    head, _, tail = original.rpartition("}")
    modified = (
        head
        + "\n\tpublic Map<String, Object> selectBackfillProbe(Map<String, Object> p);\n}"
        + tail
    )
    dst.write_text(modified)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "commit 2: add probe method", date=COMMIT2_DATE)

    return repo
