"""
seed_councilors.py

Seeds the councilors table from the current roster published at
city.waltham.ma.us/1341/City-Council. There's no clean structured feed for
this (it's a CMS content table), so the roster is hand-transcribed here —
re-run after an election or a mid-term resignation/appointment to update it.

Minutes refer to councilors by last name only (e.g. "Councillor Katz"), so
last_name is the join key used everywhere else in the pipeline.

Note: council membership changed in the November 2025 election. Kathleen
McMenimen (50-year councilor) and Tom Stanley (At-Large) both left the
council; Tim King and Emma Tzioumis joined as of the January 2026 term, and
the presidency passed from McLaughlin to Logan. McMenimen and Stanley are
kept here as inactive=0 so 2025 minutes (which reference them by last name
in vote/attendance rolls) still resolve correctly.
"""

from db import get_conn, init_db

# (last_name, full_name, seat, active) — last_name must match how it appears in minutes.
ROSTER = [
    ("Bradley-MacArthur", "Colleen Bradley-MacArthur", "At-Large", 1),
    ("Brasco", "Paul J. Brasco", "At-Large", 1),
    ("King", "Tim King", "At-Large", 1),
    ("LeBlanc", "Randall J. LeBlanc", "At-Large (Vice President)", 1),
    ("Tzioumis", "Emma Tzioumis", "At-Large", 1),
    ("Vidal", "Carlos A. Vidal", "At-Large", 1),
    ("LaFauci", "Anthony LaFauci", "Ward 1", 1),
    ("Dunn", "Caren Dunn", "Ward 2", 1),
    ("Hanley", "Bill Hanley", "Ward 3", 1),
    ("McLaughlin", "John J. McLaughlin", "Ward 4", 1),
    ("LaCava", "Joseph P. LaCava", "Ward 5", 1),
    ("Durkee", "Sean Durkee", "Ward 6", 1),
    ("Katz", "Paul S. Katz", "Ward 7", 1),
    ("Harris", "Cathyann Harris", "Ward 8", 1),
    ("Logan", "Robert G. Logan", "Ward 9 (President)", 1),
    # Former councilors, not re-elected/didn't run in Nov 2025 -- kept for
    # historical vote resolution, not shown as current members.
    ("McMenimen", "Kathleen B. McMenimen", "At-Large (former, through 2025)", 0),
    ("Stanley", "Thomas Stanley", "At-Large (former, through 2025)", 0),
]


def seed() -> None:
    init_db()
    conn = get_conn()
    for last_name, full_name, seat, active in ROSTER:
        conn.execute(
            """
            INSERT INTO councilors (last_name, full_name, seat, active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(last_name) DO UPDATE SET
                full_name = excluded.full_name,
                seat = excluded.seat,
                active = excluded.active
            """,
            (last_name, full_name, seat, active),
        )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM councilors").fetchone()[0]
    conn.close()
    print(f"Seeded/updated {len(ROSTER)} councilors. Table now has {count} rows.")


if __name__ == "__main__":
    seed()
