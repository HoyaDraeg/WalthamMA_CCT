"""
Unit tests for parse_minutes.py, the most bug-prone module in this repo --
almost every fix made during development came from a real PDF-extraction
artifact (page numbers landing mid-sentence, a spurious mid-word space, a
Private-Use-Area bullet glyph, a "Councillors," label artifact). Each of
those gets its own regression test here, plus full parse_meeting() /
parse_committee_of_whole_meeting() integration tests against small,
hand-traceable synthetic minutes text in both real formats this parser
supports.
"""

import parse_minutes as pm

KNOWN = {"Jones", "Smith", "Lee", "Adams", "Vidal", "LaCava", "McLaughlin", "LeBlanc", "Katz"}


# ---------------------------------------------------------------- normalize()

def test_normalize_strips_bare_page_number_lines():
    text = "Roll call requested.\n5\n\nIn favor: Jones."
    assert pm.normalize(text) == "Roll call requested. In favor: Jones."


def test_normalize_does_not_strip_real_numbered_items():
    # a real item marker keeps the number and following word glued on one
    # line ("1.The Mayor") -- normalize() must never eat these.
    text = "1.The Mayor requested funds."
    assert "1." in pm.normalize(text)


def test_normalize_decomposes_ligatures():
    text = "The oﬃce staﬀ met."
    assert pm.normalize(text) == "The office staff met."


# ---------------------------------------------------------- make_extract_names()

def test_extract_names_basic_list():
    extract = pm.make_extract_names(KNOWN)
    assert extract("Jones, Smith and Lee") == ["Jones", "Smith", "Lee"]


def test_extract_names_strips_councillors_label_artifact():
    # real minutes sometimes read "In favor: Councillors, Brasco, Dunn..."
    # -- the label word right after the colon must not be treated as a name.
    extract = pm.make_extract_names(KNOWN)
    assert extract("Councillors, Jones, Smith") == ["Jones", "Smith"]


def test_extract_names_strips_title_prefixes():
    extract = pm.make_extract_names(KNOWN)
    assert extract("President Jones, Vice-President Smith, VP Lee") == ["Jones", "Smith", "Lee"]


def test_extract_names_ignores_none_and_unknown_tokens():
    extract = pm.make_extract_names(KNOWN)
    assert extract("None") == []
    assert extract("") == []
    assert extract("Mendonsa, Jones") == ["Jones"]  # Mendonsa isn't a councilor


def test_extract_names_deduplicates():
    extract = pm.make_extract_names(KNOWN)
    assert extract("Jones, Jones and Smith") == ["Jones", "Smith"]


# --------------------------------------------------------------- find_roll_calls()

def test_find_roll_calls_basic_block():
    extract = pm.make_extract_names(KNOWN)
    text = (
        "Roll call required. In favor: Councillors Jones, Smith. Opposed: Adams. "
        "Absent: Vidal. Presiding: Lee. Approved 2-1-1-1."
    )
    blocks = pm.find_roll_calls(text, extract)
    assert len(blocks) == 1
    assert blocks[0]["votes"] == {"Jones": "yes", "Smith": "yes", "Adams": "no", "Vidal": "absent", "Lee": "presiding"}


def test_find_roll_calls_label_order_independent():
    # real minutes vary the order of Absent/Recused/Presiding -- both
    # orderings below must parse to the same vote set.
    extract = pm.make_extract_names(KNOWN)
    text_a = "Roll call required. In favor: Jones. Opposed: None. Recused: Smith. Absent: Adams. Presiding: Lee."
    text_b = "Roll call required. In favor: Jones. Opposed: None. Absent: Adams. Recused: Smith. Presiding: Lee."
    votes_a = pm.find_roll_calls(text_a, extract)[0]["votes"]
    votes_b = pm.find_roll_calls(text_b, extract)[0]["votes"]
    expected = {"Jones": "yes", "Smith": "recused", "Adams": "absent", "Lee": "presiding"}
    assert votes_a == expected
    assert votes_b == expected


def test_find_roll_calls_tolerates_kerning_space_artifact():
    # pypdf occasionally splits "requested" into "re quested" -- the
    # trigger regex must still match, or the whole vote silently disappears.
    extract = pm.make_extract_names(KNOWN)
    text = "Roll call re quested . In favor: Jones. Opposed: None."
    blocks = pm.find_roll_calls(text, extract)
    assert len(blocks) == 1
    assert blocks[0]["votes"] == {"Jones": "yes"}


def test_find_roll_calls_presiding_does_not_override_actual_vote():
    # a presiding officer who's also listed as voting keeps their real
    # vote -- setdefault(), not overwrite.
    extract = pm.make_extract_names(KNOWN)
    text = "Roll call required. In favor: Jones, Smith. Opposed: None. Presiding: Smith."
    votes = pm.find_roll_calls(text, extract)[0]["votes"]
    assert votes["Smith"] == "yes"


# -------------------------------------------------------------------- BULLET_RE

def test_bullet_regex_matches_both_glyph_variants():
    assert pm.BULLET_RE.search("• item") is not None
    assert pm.BULLET_RE.search(" item") is not None  # Wingdings-style PUA bullet


# ---------------------------------------------------------------- parse_meeting()

MAIN_FORMAT_SAMPLE = """
Waltham City Council
Minutes of the Meeting of
January 1, 2026
President Smith called the meeting to order at 7:30 p.m.
Present: Jones, Lee, Adams and President Smith.
Absent: Vidal
Councillor Jones moved approval of the regular City Council meeting minutes of December 15, 2025. The motion was approved by a voice vote.
Communications from the Mayor
1.The Mayor respectfully requested an appropriation in the amount of $10,000 for road repairs. Referred to the Finance Committee.
2.The Mayor requested approval of new signage. The motion was approved by a voice vote.
Committee Reports
Finance
1.The Finance Committee recommended the appropriation be approved. Councillor Jones moved that the action of the Committee be the action of the Council. Roll call required.
In favor: Councillors Jones and Lee. Opposed: Adams. Absent: Vidal. Presiding: Smith.
The matter was approved 2-1-1-1.
"""


def test_parse_meeting_attendance():
    result = pm.parse_meeting(MAIN_FORMAT_SAMPLE, KNOWN)
    attendance = dict(result["attendance"])
    assert attendance == {"Jones": "present", "Lee": "present", "Adams": "present", "Smith": "present", "Vidal": "absent"}


def test_parse_meeting_finds_roll_call_item_with_correct_votes():
    result = pm.parse_meeting(MAIN_FORMAT_SAMPLE, KNOWN)
    roll_call_items = [i for i in result["items"] if i["vote_type"] == "roll_call"]
    assert len(roll_call_items) == 1
    item = roll_call_items[0]
    assert item["votes"] == {"Jones": "yes", "Lee": "yes", "Adams": "no", "Vidal": "absent", "Smith": "presiding"}
    assert item["committee"] == "Finance"
    assert item["section"] == "Committee Reports"
    assert item["sponsor_last_name"] == "Jones"


def test_parse_meeting_communications_item_referred_to_committee():
    result = pm.parse_meeting(MAIN_FORMAT_SAMPLE, KNOWN)
    mayor_items = [i for i in result["items"] if i["section"] == "Communications from the Mayor"]
    assert len(mayor_items) == 2
    referred = next(i for i in mayor_items if "repairs" in i["description"])
    assert referred["disposition"] == "referred"
    assert referred["committee"] == "Finance"


def test_parse_meeting_voice_vote_item_has_no_votes():
    result = pm.parse_meeting(MAIN_FORMAT_SAMPLE, KNOWN)
    voice_items = [i for i in result["items"] if i["vote_type"] == "voice"]
    assert len(voice_items) >= 1
    assert all(item["votes"] == {} for item in voice_items)


# ------------------------------------------- parse_committee_of_whole_meeting()

COW_FORMAT_SAMPLE = """Committee of the Whole
Minutes of the Meeting
January 1, 2026

Vice-President LaCava called the meeting to Order at 8:00pm in the City Council
Chamber.

Vice-President LaCava requested that a roll call be taken to record the attendance for
the meeting. The COW Clerk called the roll - Councillors Jones, Smith, Adams,
and Vice-President LaCava were all present in-person. Councillor Vidal was absent.

President McLaughlin moved to go into Executive Session. The motion was adopted on a roll call vote
of 3 in favor (Jones, Smith, President McLaughlin), 1 not present at the ES meeting (Adams) and LaCava presiding.

A motion by Councillor Jones to adjourn was adopted on a voice vote, and Vice-
President LaCava declared the meeting adjourned at 8:07pm.

Paul G. Centofanti - Clerk to the Committee of the Whole
"""

COW_KNOWN = KNOWN | {"McLaughlin"}


def test_cow_header_detection():
    assert pm.COW_HEADER_RE.match(COW_FORMAT_SAMPLE.strip())
    assert not pm.COW_HEADER_RE.match(MAIN_FORMAT_SAMPLE.strip())


def test_parse_cow_meeting_attendance():
    result = pm.parse_committee_of_whole_meeting(COW_FORMAT_SAMPLE, COW_KNOWN)
    attendance = dict(result["attendance"])
    assert attendance == {"Jones": "present", "Smith": "present", "Adams": "present", "LaCava": "present", "Vidal": "absent"}


def test_parse_cow_meeting_inline_roll_call_votes():
    result = pm.parse_committee_of_whole_meeting(COW_FORMAT_SAMPLE, COW_KNOWN)
    roll_call_items = [i for i in result["items"] if i["vote_type"] == "roll_call"]
    assert len(roll_call_items) == 1
    votes = roll_call_items[0]["votes"]
    # "not present at the ES meeting" is a variant absence phrasing and
    # must fold into the "absent" bucket, same as a plain "absent" clause.
    assert votes == {"Jones": "yes", "Smith": "yes", "McLaughlin": "yes", "Adams": "absent", "LaCava": "presiding"}


def test_parse_cow_meeting_voice_vote_item():
    result = pm.parse_committee_of_whole_meeting(COW_FORMAT_SAMPLE, COW_KNOWN)
    voice_items = [i for i in result["items"] if i["vote_type"] == "voice"]
    assert len(voice_items) >= 1


# ----------------------------------------------------------- detect_disposition()

def test_detect_disposition_variants():
    assert pm.detect_disposition("The motion was approved by a voice vote.")[0] == "approved"
    assert pm.detect_disposition("Referred to the Finance Committee.") == ("referred", "Finance")
    assert pm.detect_disposition("The matter was tabled.")[0] == "tabled"
    assert pm.detect_disposition("Councillor Harris moved to withdraw the matter.")[0] == "withdrawn"


# -------------------------------------------------------------- extract_remarks()

def test_extract_remarks_captures_spoke_and_moved():
    extract = pm.make_extract_names(KNOWN)
    text = "Councillor Jones moved approval. Councillor Smith spoke on the matter."
    remarks = pm.extract_remarks(text, extract)
    types_by_name = {name: remark_type for remark_type, name, _ in remarks}
    assert types_by_name["Jones"] == "moved"
    assert types_by_name["Smith"] == "spoke"
