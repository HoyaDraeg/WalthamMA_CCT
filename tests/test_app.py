"""
App-level tests. compute_awards() is tested directly against the seeded
fixture for facts that are simple, exact counts (attendance/dissent/
recusal/sponsorship -- no risk of a hand-arithmetic mistake on my part).
The two similarity-based awards are checked structurally rather than for
an exact winner, since that depends on multi-step cosine-similarity math
that's already precisely covered in test_build_similarity.py -- re-deriving
that by hand here would just be a second, more fragile copy of the same
check.

The AppTest smoke tests matter more than they look: the very first one
written for this app (a plain "does every page render") caught a real
NaN-vs-None bug in production before this test suite existed. Streamlit's
cache_data is process-global and keyed by function+args, not by which
temp DB is active, so `clear_streamlit_cache` below is required for test
isolation -- without it, a later test can silently see an earlier test's
cached DB results.
"""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import awards

APP_PATH = str(Path(__file__).parent.parent / "app.py")


@pytest.fixture(autouse=True)
def clear_streamlit_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


# ------------------------------------------------------------- compute_awards()

def test_perfect_attendance_among_active_councilors_only(seeded_db):
    ids = seeded_db["councilor_ids"]
    result = awards.compute_awards()
    winners, min_absent = result["perfect_attendance"]
    assert min_absent == 0
    assert set(winners) == {ids["Anderson"], ids["Baker"], ids["Clark"]}
    assert ids["Davis"] not in winners  # inactive -- excluded from the active-only scope
    assert ids["Evans"] not in winners  # zero attendance records -- can't "win" by default


def test_most_dissenting(seeded_db):
    ids = seeded_db["councilor_ids"]
    winners, max_no = awards.compute_awards()["most_dissenting"]
    assert max_no == 2
    assert winners == [ids["Clark"]]


def test_most_recused_empty_when_no_recusals_recorded(seeded_db):
    winners, max_recused = awards.compute_awards()["most_recused"]
    assert winners == []
    assert max_recused == 0


def test_high_achiever_three_way_tie(seeded_db):
    ids = seeded_db["councilor_ids"]
    winners, max_sponsor = awards.compute_awards()["high_achiever"]
    assert max_sponsor == 1
    assert set(winners) == {ids["Anderson"], ids["Baker"], ids["Clark"]}


def test_similarity_awards_are_scoped_to_active_councilors(seeded_db):
    ids = seeded_db["councilor_ids"]
    result = awards.compute_awards()

    pairs, top_score = result["most_similar"]
    assert pairs, "expected at least one most-similar pair"
    assert 0 <= top_score <= 1
    for a, b in pairs:
        assert ids["Davis"] not in (a, b)

    diff_ids, min_avg = result["most_different"]
    assert diff_ids
    assert 0 <= min_avg <= 1
    assert ids["Davis"] not in diff_ids


# ----------------------------------------------------------------- AppTest smoke

PAGES = ["Councilor Profile", "Compare Councilors", "Similarity Map", "Topic Search", "Awards", "Chat"]


def test_default_page_loads_without_exception(seeded_db):
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception


@pytest.mark.parametrize("page_name", PAGES)
def test_each_page_loads_without_exception(seeded_db, page_name):
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value(page_name).run()
    assert not at.exception


def test_topic_search_with_query_does_not_raise(seeded_db):
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Topic Search").run()
    at.text_input[0].set_value("repairs").run()
    assert not at.exception


def test_compare_councilors_selects_and_renders(seeded_db):
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Compare Councilors").run()
    options = at.selectbox[0].options
    assert not at.exception
    assert len(options) >= 2


def test_chat_end_to_end_through_the_ui(seeded_db):
    at = AppTest.from_file(APP_PATH, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Chat").run()
    at.chat_input[0].set_value("How did Clark vote on repairs?").run()
    assert not at.exception
    assert at.session_state["chat_history"], "expected a chat entry to be recorded"
