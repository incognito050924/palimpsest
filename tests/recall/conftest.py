"""Live-Neo4j test rig for GraphRAG recall (n4-impl-recall).

Same hermetic pattern as ``tests/kg/conftest.py``: a single Neo4j 5 Community
container per session (testcontainers, auto lifecycle). The fixture graph is the
n2 ``commute`` slice, extracted by the real ``extract`` and ingested via the real
``create_constraints`` + ``ingest`` — no external corpus, no hand-built graph.
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

# Fixed, deterministic provenance so recall tests do not depend on live git.
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


@pytest.fixture(scope="session")
def recall_db(neo4j_driver, ir):
    """A live DB with the fixture IR ingested once (constraints + ingest).

    Session-scoped: recall is read-only, so the graph is built once and shared.
    """
    with neo4j_driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    create_constraints(neo4j_driver)
    ingest(neo4j_driver, ir)
    return neo4j_driver
