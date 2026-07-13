"""Static extraction: source tree -> palimpsest IR (no Neo4j here).

Per-language extractors each own their ``queries/<lang>/*.scm`` (ADR-20260706
§결정6). ``extract`` stays the Java extractor (unchanged, backward-compatible);
Kotlin is reachable as ``extract_kotlin`` and via the ``EXTRACTORS_BY_EXT``
dispatch keyed on source-file extension.
"""

from palimpsest.extract.java import extract as extract_java
from palimpsest.extract.kotlin import extract as extract_kotlin
from palimpsest.extract.python import extract as extract_python
from palimpsest.extract.provenance import changed_paths, read_provenance

# Backward-compatible default: `extract` remains the Java extractor.
extract = extract_java

# Language dispatch by source-file extension.
EXTRACTORS_BY_EXT = {".java": extract_java, ".kt": extract_kotlin, ".py": extract_python}

__all__ = [
    "extract",
    "extract_java",
    "extract_kotlin",
    "extract_python",
    "EXTRACTORS_BY_EXT",
    "read_provenance",
    "changed_paths",
]
