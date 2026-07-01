"""Static extraction: Java source tree -> palimpsest IR (no Neo4j here)."""

from palimpsest.extract.java import extract
from palimpsest.extract.provenance import read_provenance

__all__ = ["extract", "read_provenance"]
