"""Live-Neo4j test rig for KG ingest (n3-impl-kg-ingest).

Hermetic: a single Neo4j 5 Community container is stood up per test session via
``testcontainers`` (auto lifecycle). The fixture IR is produced by running the
real ``extract`` over the n2 fixture tree — no external corpus path.
"""

import os
import subprocess
from pathlib import Path

import pytest

from palimpsest.extract import extract
from palimpsest.ir import Provenance
from palimpsest.kg import create_constraints, ingest


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

# Reuse the n2 extraction fixtures (the `commute` feature slice).
FIXTURES = Path(__file__).parents[1] / "extract" / "fixtures"

# Fixed, deterministic provenance so ingest tests do not depend on live git.
PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

NEO4J_IMAGE = "neo4j:5-community"
NEO4J_PASSWORD = "palimpsest-test"


@pytest.fixture(scope="session")
def ir():
    """A small, real IR built by extracting the fixture Java tree."""
    return extract(FIXTURES, PROV, repo_name="EcoleTreeSystems")


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
def clean_db(neo4j_driver):
    """A wiped DB with the ontology constraints in place (function scope)."""
    with neo4j_driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    create_constraints(neo4j_driver)
    return neo4j_driver


@pytest.fixture
def ingested(clean_db, ir):
    """A clean DB with the fixture IR ingested once."""
    ingest(clean_db, ir)
    return clean_db
