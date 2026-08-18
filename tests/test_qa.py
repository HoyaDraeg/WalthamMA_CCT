"""
Tests for qa.py, the offline (no-LLM) chat backend. The pure helper
functions (name/intent/committee/topic detection) are tested directly
against a small in-memory councilors DataFrame -- no database needed,
these are genuinely fast unit tests. answer_question() itself is tested
end-to-end against the seeded_db fixture since it needs real data to
query.
"""

import pandas as pd
import pytest

import qa

COUNCILORS_DF = pd.DataFrame([
    {"id": 1, "last_name": "LaCava", "full_name": "Joseph LaCava", "seat": "Ward 5", "active": 1},
    {"id": 2, "last_name": "Katz", "full_name": "Paul Katz", "seat": "Ward 7", "active": 1},
    {"id": 3, "last_name": "Logan", "full_name": "Robert Logan", "seat": "Ward 9", "active": 1},
])


# ------------------------------------------------------------- find_councilors()

def test_find_councilors_matches_last_name():
    assert qa.find_councilors("How did LaCava vote?", COUNCILORS_DF) == [1]


def test_find_councilors_matches_first_name_too():
    assert qa.find_councilors("What did Joseph say?", COUNCILORS_DF) == [1]


def test_find_councilors_no_match_returns_empty():
    assert qa.find_councilors("What happened at the meeting?", COUNCILORS_DF) == []


def test_find_councilors_finds_multiple_in_mention_order():
    result = qa.find_councilors("Compare Logan and LaCava on zoning", COUNCILORS_DF)
    assert result == [3, 1]


def test_find_councilors_does_not_false_positive_on_substring():
    # "Katz" must not match inside an unrelated word containing the same
    # letters -- word-boundary matching, not substring matching.
    df = pd.DataFrame([{"id": 1, "last_name": "Katz", "full_name": "Paul Katz", "seat": "Ward 7", "active": 1}])
    assert qa.find_councilors("The katzenjammer band played", df) == []


# ---------------------------------------------------------------- find_intents()

@pytest.mark.parametrize("question,expected_intent", [
    ("How did LaCava vote?", "vote"),
    ("Who is most similar to Logan?", "similarity"),
    ("How often is Vidal absent?", "attendance"),
    ("What has Katz sponsored?", "sponsor"),
    ("What did Durkee say about zoning?", "remarks"),
])
def test_find_intents_detects_expected_keyword(question, expected_intent):
    assert expected_intent in qa.find_intents(question)


def test_find_intents_no_keywords_returns_empty():
    assert qa.find_intents("Tell me about the Howe building") == []


# -------------------------------------------------------------- find_committee()

def test_find_committee_matches_known_committee():
    assert qa.find_committee("What has Katz sponsored in Finance?") == "Finance"


def test_find_committee_no_match_returns_none():
    assert qa.find_committee("What happened yesterday?") is None


# --------------------------------------------------------------- extract_topic()

def test_extract_topic_strips_councilor_name_and_stopwords():
    topic = qa.extract_topic("How did LaCava vote on affordable housing?", COUNCILORS_DF, [1], None)
    assert topic == "affordable housing"


def test_extract_topic_strips_filler_words():
    # regression: "usually" (and similar filler adverbs) used to leak
    # through into the FTS topic, turning a profile question into a
    # nonsense zero-result search.
    topic = qa.extract_topic("How does Vidal usually vote?", COUNCILORS_DF, [], None)
    assert "usually" not in (topic or "")


def test_extract_topic_returns_none_when_nothing_left():
    topic = qa.extract_topic("How did LaCava vote?", COUNCILORS_DF, [1], None)
    assert topic is None


# ------------------------------------------------------------- _fts_sanitize()

def test_fts_sanitize_strips_punctuation():
    assert qa._fts_sanitize("Mt. Walley Road!!") == "Walley Road"


def test_fts_sanitize_drops_short_tokens():
    assert qa._fts_sanitize("EV ID") is None  # both tokens <= 2 chars


def test_fts_sanitize_empty_input_returns_none():
    assert qa._fts_sanitize("") is None


# --------------------------------------------------------- answer_question() e2e

def test_answer_question_single_councilor_topic_vote(seeded_db):
    result = qa.answer_question("How did Clark vote on repairs?")
    assert result["tables"], "expected a results table"
    label, df = result["tables"][0]
    assert len(df) == 1
    assert df.iloc[0]["vote"] == "no"


def test_answer_question_two_councilors_compare(seeded_db):
    result = qa.answer_question("Compare Anderson and Clark")
    assert "0%" in result["answer"]  # hand-computed: 0% roll-call agreement


def test_answer_question_similarity_intent(seeded_db):
    result = qa.answer_question("Who is most similar to Anderson?")
    assert "Davis" in result["answer"]


def test_answer_question_attendance_intent(seeded_db):
    result = qa.answer_question("How often is Davis absent?")
    assert "absent from 1" in result["answer"]


def test_answer_question_sponsor_intent_with_committee_filter(seeded_db):
    result = qa.answer_question("What has Clark sponsored in Ordinances and Rules?")
    label, df = result["tables"][0]
    assert len(df) == 1
    assert "setback" in df.iloc[0]["description"]


def test_answer_question_no_councilor_no_topic_gives_help_text(seeded_db):
    # a question made entirely of stopwords leaves no topic at all --
    # distinct from a question with real (if unmatched) words, which
    # correctly falls through to a topic search instead (see the general
    # no-councilor branch, exercised by other tests).
    result = qa.answer_question("What is this?")
    assert "keyword search" in result["answer"]


def test_answer_question_includes_minutes_url_in_tables(seeded_db):
    result = qa.answer_question("What has Anderson sponsored?")
    label, df = result["tables"][0]
    assert "minutes_url" in df.columns
    assert df.iloc[0]["minutes_url"] is not None
