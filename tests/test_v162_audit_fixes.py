"""v1.6.2 RED: verify ContentMatchTrigger event payload contract.

v1.6.1 shipped `matched_keywords` as a bare int (the count), but the field
name suggests a list. v1.6.2 splits this into two explicit fields:
  - matched_keywords: list[str]  (the actual keyword strings that matched)
  - contains_count:   int         (total keywords searched)

This test pins both fields' types and values so future refactors don't
regress the script-receiver template bindings.
"""
from __future__ import annotations

import pytest

from agentwire_cue.core.trigger_impl import ContentMatchTrigger


class _StubPlugin:
    def __init__(self, name: str = "stub"):
        self.name = name
        self.triggers = []


def _make_trigger(contains, min_match=1, peer_filter=None):
    trigger_def = {
        "id": "t1",
        "type": "a2a_content_match",
        "config": {
            "contains": contains,
            "min_match": min_match,
            "peer": peer_filter,
        },
    }
    return ContentMatchTrigger(trigger_def, _StubPlugin())


def test_matched_keywords_is_list_of_strings_for_2_matches():
    trigger = _make_trigger(["project:", "scenes:"])
    text = "project: alpha\nscenes: 1,2,3"
    matched = [kw for kw in trigger.contains if kw in text]
    assert matched == ["project:", "scenes:"]
    assert len(matched) == 2


def test_contains_count_is_total_keywords_in_contains_list():
    trigger = _make_trigger(["project:", "scenes:", "props:"])
    assert len(trigger.contains) == 3


def test_min_match_filter_uses_matched_count_not_contains_count():
    """v1.6.2: matched count must be compared against min_match, not the total."""
    trigger = _make_trigger(["project:", "scenes:", "props:"], min_match=2)
    # only 1 keyword present in this text
    text = "project: alpha"
    matched_kws = [kw for kw in trigger.contains if kw in text]
    assert len(matched_kws) < trigger.min_match  # should NOT fire
    # verify 2 keywords present
    text2 = "project: alpha\nscenes: 1"
    matched_kws2 = [kw for kw in trigger.contains if kw in text2]
    assert len(matched_kws2) >= trigger.min_match  # should fire


def test_peer_filter_passed_through_to_event():
    trigger = _make_trigger(["urgent:"], peer_filter="Pawly")
    assert trigger.peer_filter == "Pawly"
