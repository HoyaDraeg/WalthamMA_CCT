"""
awards.py

Computes the Councilor Awards superlatives (Perfect Attendance, Most
Dissenting, Most Recused, Most Similar, Most Different, High Achiever).
Split out from app.py so it can be imported and tested directly without
pulling in app.py's own module-level script code (which runs a live
database query at import time, as any normal top-to-bottom Streamlit
script does) -- tests/test_app.py imports this module directly, and
otherwise only ever touches app.py through Streamlit's AppTest harness,
which runs the script in a proper simulated context.
"""

import pandas as pd
import streamlit as st

from build_similarity import compute_similarity
from db import get_conn


@st.cache_data(ttl=60)
def compute_awards() -> dict:
    """Superlatives over the current active councilors (same scope as the
    Councilor Profile / Compare dropdowns elsewhere in the app)."""
    conn = get_conn()
    councilors_df = pd.read_sql_query("SELECT * FROM councilors WHERE active = 1", conn)
    ids = councilors_df["id"].tolist()

    att = pd.read_sql_query("SELECT councilor_id, status, COUNT(*) n FROM attendance GROUP BY councilor_id, status", conn)
    att_pivot = att.pivot(index="councilor_id", columns="status", values="n").reindex(ids).fillna(0)
    for col in ("present", "absent"):
        if col not in att_pivot.columns:
            att_pivot[col] = 0

    votes = pd.read_sql_query("SELECT councilor_id, vote, COUNT(*) n FROM votes GROUP BY councilor_id, vote", conn)
    votes_pivot = votes.pivot(index="councilor_id", columns="vote", values="n").reindex(ids).fillna(0)
    for col in ("no", "recused"):
        if col not in votes_pivot.columns:
            votes_pivot[col] = 0

    sponsor = (
        pd.read_sql_query(
            "SELECT sponsor_councilor_id AS councilor_id, COUNT(*) n FROM agenda_items "
            "WHERE sponsor_councilor_id IS NOT NULL GROUP BY sponsor_councilor_id",
            conn,
        )
        .set_index("councilor_id")["n"]
        .reindex(ids)
        .fillna(0)
    )
    conn.close()

    sim = compute_similarity()
    combined = sim["combined_similarity"].loc[ids, ids]

    # 1. Perfect attendance: fewest absences, among councilors who have
    # actually got at least one attendance record (so someone with zero
    # tracked meetings can't "win" by default).
    has_att = att_pivot[(att_pivot["present"] + att_pivot["absent"]) > 0]
    min_absent = has_att["absent"].min() if not has_att.empty else None
    perfect_attendance_ids = has_att.index[has_att["absent"] == min_absent].tolist() if min_absent is not None else []

    # 2. Most dissenting: most "no" votes.
    max_no = votes_pivot["no"].max() if not votes_pivot.empty else 0
    most_dissenting_ids = votes_pivot.index[votes_pivot["no"] == max_no].tolist() if max_no > 0 else []

    # 3. Most recused.
    max_recused = votes_pivot["recused"].max() if not votes_pivot.empty else 0
    most_recused_ids = votes_pivot.index[votes_pivot["recused"] == max_recused].tolist() if max_recused > 0 else []

    # 4. Most similar: the single highest-scoring pair (or pairs, if tied).
    pairs = []
    for i, a in enumerate(combined.index):
        for b in combined.index[i + 1:]:
            pairs.append((combined.loc[a, b], a, b))
    pairs.sort(key=lambda p: p[0], reverse=True)
    top_score = pairs[0][0] if pairs else None
    most_similar_pairs = [(a, b) for score, a, b in pairs if score == top_score] if pairs else []

    # 5. Most different than everyone else: lowest AVERAGE similarity to
    # all other councilors (an outlier from the whole group), not just the
    # single most-dissimilar pair -- that's a distinct thing from #4.
    avg_sim = combined.apply(lambda row: row.drop(row.name).mean(), axis=1)
    min_avg = avg_sim.min() if not avg_sim.empty else None
    most_different_ids = avg_sim.index[avg_sim == min_avg].tolist() if min_avg is not None else []

    # 6. High achiever: most sponsored/moved items.
    max_sponsor = sponsor.max() if not sponsor.empty else 0
    high_achiever_ids = sponsor.index[sponsor == max_sponsor].tolist() if max_sponsor > 0 else []

    return {
        "councilors_df": councilors_df.set_index("id"),
        "attendance_table": has_att,
        "votes_table": votes_pivot,
        "sponsor_table": sponsor,
        "avg_similarity": avg_sim,
        "perfect_attendance": (perfect_attendance_ids, min_absent),
        "most_dissenting": (most_dissenting_ids, max_no),
        "most_recused": (most_recused_ids, max_recused),
        "most_similar": (most_similar_pairs, top_score),
        "most_different": (most_different_ids, min_avg),
        "high_achiever": (high_achiever_ids, max_sponsor),
    }
