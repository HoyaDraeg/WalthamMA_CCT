"""Schema sanity checks -- catches typos/renames in db.py's SCHEMA before
they surface as confusing "no such column" errors somewhere downstream."""

import db


def test_init_db_creates_all_tables(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "schema_test.db")
    db.init_db()
    conn = db.get_conn()
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    expected = {"councilors", "meetings", "attendance", "agenda_items", "votes", "remarks"}
    assert expected.issubset(tables)


def test_init_db_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "schema_test2.db")
    db.init_db()
    db.init_db()  # must not raise on a second run (upsert/refresh workflow depends on this)


def test_agenda_items_fts_trigger_stays_in_sync(seeded_db):
    conn = db.get_conn()
    n_items = conn.execute("SELECT COUNT(*) FROM agenda_items").fetchone()[0]
    n_fts = conn.execute("SELECT COUNT(*) FROM agenda_items_fts").fetchone()[0]
    assert n_items == n_fts == 3

    hits = conn.execute(
        "SELECT COUNT(*) FROM agenda_items_fts WHERE agenda_items_fts MATCH 'repairs'"
    ).fetchone()[0]
    assert hits == 1  # only item A1 mentions "repairs"
    conn.close()
