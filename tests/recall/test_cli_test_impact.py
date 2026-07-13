"""Hermetic CLI wiring for the `test-impact` subcommand (#18 item 1).

Mirrors how churn/cochange are thin CLI wrappers over a recall channel: the channel
itself is covered by test_recall_test_impact.py, so here we prove only the CLI surface —
the subcommand exists, parses --depth/--limit, routes to recall_test_impact with those
args, and renders items + gaps + confidence in separated sections. The driver and the
recall call are stubbed, so no Neo4j is needed.
"""

import contextlib

from palimpsest import cli

SEED = "app.Widget#render()"


def _canned_result():
    """A _result-shaped dict with one grounded test-caller item, a gap, confidence."""
    return {
        "items": [{
            "id": "app.WidgetDirectTest#rendersDirectly()",
            "kind": "Method",
            "qualified_name": "app.WidgetDirectTest#rendersDirectly()",
            "relation": "CALLS",
            "depth": 1,
            "sources": {
                "source_commit": "c20b7332",
                "path": "src/test/java/app/WidgetDirectTest.java",
                "start_line": 8, "end_line": 11,
                "committed_at": "2025-09-03T16:22:54+09:00",
            },
        }],
        "sources": [], "summaries": [], "risks": [], "decisions": [], "relations": [],
        "gaps": ["static CALLS is a lower bound: completeness is not claimed"],
        "confidence": 1.0,
        "expand_handle": None,
    }


def _patch(monkeypatch, sink, recall_fn=None):
    @contextlib.contextmanager
    def _fake_driver():
        yield "DRIVER"

    def _fake_recall(driver, method_id, depth=10, limit=25):
        sink["args"] = (driver, method_id, depth, limit)
        return _canned_result()

    monkeypatch.setattr(cli, "_driver", _fake_driver)
    monkeypatch.setattr(cli, "recall_test_impact", recall_fn or _fake_recall)


def test_test_impact_subcommand_routes_and_renders(monkeypatch, capsys):
    """The subcommand parses the seed + --depth/--limit, routes them to the channel,
    and renders the returned items (grounded), gaps, and confidence in own sections."""
    sink = {}
    _patch(monkeypatch, sink)

    assert cli.main(["test-impact", SEED, "--depth", "3", "--limit", "5"]) == 0

    # routed to the channel with the parsed seed + flags
    assert sink["args"] == ("DRIVER", SEED, 3, 5)

    out = capsys.readouterr().out
    assert SEED in out                                        # header names the seed
    assert "app.WidgetDirectTest#rendersDirectly()" in out   # the test-caller item
    assert "src/test/java/app/WidgetDirectTest.java" in out   # grounded source
    assert "GAPS (1)" in out                                  # gaps in own section
    assert "lower bound" in out                               # the honesty gap surfaced
    assert "CONFIDENCE:" in out                               # confidence surfaced


def test_test_impact_defaults_match_channel(monkeypatch, capsys):
    """No --depth/--limit -> the channel's own defaults (depth=10, limit=25)."""
    sink = {}
    _patch(monkeypatch, sink)
    assert cli.main(["test-impact", SEED]) == 0
    assert sink["args"][2:] == (10, 25)
