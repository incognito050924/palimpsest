"""Pure-unit tests for the CLI's DISPLAY-ONLY semantic annotation flattening.

Covers the coverage axis surfacing consistently with faithfulness (issue #6): a
coverage_verdict is shown when present, and a coverage-only entry is not dropped.
"""

from palimpsest.cli import _semantic_annotations


def test_coverage_verdict_surfaces_and_coverage_only_entry_is_not_dropped():
    semantic = {
        "summaries": [
            {
                "coverage_verdict": {"verdict": "incomplete", "judge": "ditto"},
                "semantic_verdict": None,  # coverage-only entry
                "code_bound_at": "c1",
            }
        ]
    }
    lines = _semantic_annotations(semantic)
    assert len(lines) == 1  # guard no longer drops a coverage-only entry
    assert "coverage=incomplete" in lines[0]
    assert "verdict=None" in lines[0]


def test_no_coverage_token_when_absent_backward_compatible():
    semantic = {
        "summaries": [
            {"semantic_verdict": {"verdict": "faithful"}, "code_bound_at": "c1"}
        ]
    }
    lines = _semantic_annotations(semantic)
    assert len(lines) == 1
    assert "coverage=" not in lines[0]  # no noise when there is no coverage verdict
    assert "verdict=faithful" in lines[0]
