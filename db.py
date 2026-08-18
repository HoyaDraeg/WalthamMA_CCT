"""
db.py

SQLite schema and connection helper for the Waltham City Council tracker.
All other scripts import get_conn() / init_db() from here rather than
touching sqlite3 directly, so the schema lives in exactly one place.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "waltham_council.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS councilors (
    id INTEGER PRIMARY KEY,
    last_name TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    seat TEXT NOT NULL,          -- e.g. "Ward 1", "At-Large", "President"
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY,
    doc_id TEXT NOT NULL UNIQUE,      -- AgendaCenter id, e.g. "06222026-623"
    meeting_date TEXT NOT NULL,       -- ISO YYYY-MM-DD
    title TEXT,
    agenda_url TEXT,
    minutes_url TEXT,
    minutes_pdf_path TEXT,
    agenda_pdf_path TEXT,
    raw_text TEXT,
    parsed_at TEXT,
    scraped_at TEXT
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id),
    councilor_id INTEGER NOT NULL REFERENCES councilors(id),
    status TEXT NOT NULL   -- present | absent
);

CREATE TABLE IF NOT EXISTS agenda_items (
    id INTEGER PRIMARY KEY,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id),
    section TEXT NOT NULL,        -- e.g. "Finance", "Ordinances and Rules"
    item_number INTEGER,
    description TEXT NOT NULL,
    disposition TEXT,             -- approved | referred | tabled | withdrawn | failed
    committee TEXT,               -- committee it was referred to / reported from
    sponsor_councilor_id INTEGER REFERENCES councilors(id),
    vote_type TEXT                -- voice | roll_call
);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY,
    agenda_item_id INTEGER NOT NULL REFERENCES agenda_items(id),
    councilor_id INTEGER NOT NULL REFERENCES councilors(id),
    vote TEXT NOT NULL   -- yes | no | recused | absent | presiding
);

CREATE TABLE IF NOT EXISTS remarks (
    id INTEGER PRIMARY KEY,
    agenda_item_id INTEGER NOT NULL REFERENCES agenda_items(id),
    councilor_id INTEGER NOT NULL REFERENCES councilors(id),
    remark_type TEXT NOT NULL,   -- moved | spoke | asked_question | recused
    snippet TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS agenda_items_fts USING fts5(
    description,
    content='agenda_items',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS agenda_items_ai AFTER INSERT ON agenda_items BEGIN
    INSERT INTO agenda_items_fts(rowid, description) VALUES (new.id, new.description);
END;

CREATE TRIGGER IF NOT EXISTS agenda_items_ad AFTER DELETE ON agenda_items BEGIN
    INSERT INTO agenda_items_fts(agenda_items_fts, rowid, description) VALUES ('delete', old.id, old.description);
END;

CREATE TRIGGER IF NOT EXISTS agenda_items_au AFTER UPDATE ON agenda_items BEGIN
    INSERT INTO agenda_items_fts(agenda_items_fts, rowid, description) VALUES ('delete', old.id, old.description);
    INSERT INTO agenda_items_fts(rowid, description) VALUES (new.id, new.description);
END;
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
