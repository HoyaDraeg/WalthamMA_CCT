"""
Shared pytest fixtures.

`seeded_db` points db.DB_PATH at a fresh temp SQLite file per test and
loads a small, fully hand-traceable dataset (5 councilors, 2 meetings, 3
agenda items) -- every expected value asserted against it in the test
modules is computed by hand in the docstring below, not just "whatever
the code currently returns". That's what makes these regression tests
rather than change-detector tests.

Fixture data (councilor ids 1-5: Anderson, Baker, Clark, Davis, Evans;
Davis is inactive, Evans has zero recorded activity on purpose to
exercise "no data" code paths):

  Meeting 1 (2026-01-06, Finance):
    A1 "Finance appropriation for road repairs" -- roll_call, sponsor=Anderson
        votes: Anderson=yes, Baker=yes, Clark=no, Davis=absent
        remarks: Clark spoke
    A2 "Finance donation acceptance" -- voice vote, sponsor=Baker
    attendance: Anderson/Baker/Clark=present, Davis=absent

  Meeting 2 (2026-01-13, Ordinances and Rules):
    B1 "Ordinance amendment for zoning setback" -- roll_call, sponsor=Clark
        votes: Anderson=yes, Baker=no, Clark=no, Davis=yes
        remarks: Anderson moved, Davis spoke
    attendance: Anderson/Baker/Clark/Davis=present

Hand-computed roll-call vote agreement (only counting shared yes/no votes):
    Anderson-Baker: A1 agree, B1 disagree -> 1/2 = 50%
    Anderson-Clark: A1 disagree, B1 disagree -> 0/2 = 0%
    Anderson-Davis: A1 n/a (Davis absent), B1 agree -> 1/1 = 100%
    Baker-Clark:    A1 disagree, B1 agree -> 1/2 = 50%
    Baker-Davis:    A1 n/a, B1 disagree -> 0/1 = 0%
    Clark-Davis:    A1 n/a, B1 disagree -> 0/1 = 0%
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import db  # noqa: E402


@pytest.fixture
def seeded_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_waltham_council.db")
    db.init_db()
    conn = db.get_conn()

    councilors = [
        ("Anderson", "Alice Anderson", "Ward 1", 1),
        ("Baker", "Bob Baker", "Ward 2", 1),
        ("Clark", "Cara Clark", "At-Large", 1),
        ("Davis", "Dan Davis", "At-Large", 0),
        ("Evans", "Eve Evans", "Ward 3", 1),
    ]
    for last_name, full_name, seat, active in councilors:
        conn.execute(
            "INSERT INTO councilors (last_name, full_name, seat, active) VALUES (?, ?, ?, ?)",
            (last_name, full_name, seat, active),
        )
    ids = {row["last_name"]: row["id"] for row in conn.execute("SELECT id, last_name FROM councilors")}

    m1 = conn.execute(
        "INSERT INTO meetings (doc_id, meeting_date, title, minutes_url, parsed_at) VALUES (?, ?, ?, ?, ?)",
        ("test-001", "2026-01-06", "Test Meeting 1", "https://example.test/min1.pdf", "2026-01-06T00:00:00"),
    ).lastrowid
    m2 = conn.execute(
        "INSERT INTO meetings (doc_id, meeting_date, title, minutes_url, parsed_at) VALUES (?, ?, ?, ?, ?)",
        ("test-002", "2026-01-13", "Test Meeting 2", "https://example.test/min2.pdf", "2026-01-13T00:00:00"),
    ).lastrowid

    a1 = conn.execute(
        "INSERT INTO agenda_items (meeting_id, section, description, disposition, committee, sponsor_councilor_id, vote_type) "
        "VALUES (?, 'Committee Reports', 'Finance appropriation for road repairs', 'approved', 'Finance', ?, 'roll_call')",
        (m1, ids["Anderson"]),
    ).lastrowid
    a2 = conn.execute(
        "INSERT INTO agenda_items (meeting_id, section, description, disposition, committee, sponsor_councilor_id, vote_type) "
        "VALUES (?, 'Committee Reports', 'Finance donation acceptance', 'approved', 'Finance', ?, 'voice')",
        (m1, ids["Baker"]),
    ).lastrowid
    b1 = conn.execute(
        "INSERT INTO agenda_items (meeting_id, section, description, disposition, committee, sponsor_councilor_id, vote_type) "
        "VALUES (?, 'Committee Reports', 'Ordinance amendment for zoning setback', 'approved', 'Ordinances and Rules', ?, 'roll_call')",
        (m2, ids["Clark"]),
    ).lastrowid

    for cid_name, vote in [("Anderson", "yes"), ("Baker", "yes"), ("Clark", "no"), ("Davis", "absent")]:
        conn.execute("INSERT INTO votes (agenda_item_id, councilor_id, vote) VALUES (?, ?, ?)", (a1, ids[cid_name], vote))
    for cid_name, vote in [("Anderson", "yes"), ("Baker", "no"), ("Clark", "no"), ("Davis", "yes")]:
        conn.execute("INSERT INTO votes (agenda_item_id, councilor_id, vote) VALUES (?, ?, ?)", (b1, ids[cid_name], vote))

    for cid_name, status in [("Anderson", "present"), ("Baker", "present"), ("Clark", "present"), ("Davis", "absent")]:
        conn.execute("INSERT INTO attendance (meeting_id, councilor_id, status) VALUES (?, ?, ?)", (m1, ids[cid_name], status))
    for cid_name, status in [("Anderson", "present"), ("Baker", "present"), ("Clark", "present"), ("Davis", "present")]:
        conn.execute("INSERT INTO attendance (meeting_id, councilor_id, status) VALUES (?, ?, ?)", (m2, ids[cid_name], status))

    conn.execute("INSERT INTO remarks (agenda_item_id, councilor_id, remark_type, snippet) VALUES (?, ?, 'spoke', 'Clark spoke on A1')", (a1, ids["Clark"]))
    conn.execute("INSERT INTO remarks (agenda_item_id, councilor_id, remark_type, snippet) VALUES (?, ?, 'moved', 'Anderson moved B1')", (b1, ids["Anderson"]))
    conn.execute("INSERT INTO remarks (agenda_item_id, councilor_id, remark_type, snippet) VALUES (?, ?, 'spoke', 'Davis spoke on B1')", (b1, ids["Davis"]))

    conn.commit()
    conn.close()

    return {"councilor_ids": ids, "meeting_ids": {"m1": m1, "m2": m2}, "item_ids": {"a1": a1, "a2": a2, "b1": b1}}
