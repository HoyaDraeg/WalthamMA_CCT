"""
app.py

Interactive Streamlit app for the Waltham City Council tracker: topic
search, councilor profiles, a head-to-head compare view, and the
councilor similarity map/heatmap. Run with:

    py -3 -m streamlit run app.py

Data comes straight from waltham_council.db -- there's no separate cached
snapshot, so a refresh (sidebar button) is immediately reflected everywhere.
"""

import subprocess
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from build_similarity import compute_similarity, most_similar
from db import get_conn
from qa import answer_question

st.set_page_config(page_title="Waltham City Council Tracker", layout="wide")


@st.cache_data(ttl=60)
def load_councilors() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query("SELECT * FROM councilors ORDER BY active DESC, last_name", conn)
    conn.close()
    return df


@st.cache_data(ttl=60)
def load_similarity():
    return compute_similarity()


@st.cache_data(ttl=60)
def search_topic(query: str) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        """
        SELECT ai.id, m.meeting_date, ai.section, ai.committee, ai.description,
               ai.disposition, ai.vote_type
        FROM agenda_items_fts f
        JOIN agenda_items ai ON ai.id = f.rowid
        JOIN meetings m ON m.id = ai.meeting_id
        WHERE agenda_items_fts MATCH ?
        ORDER BY m.meeting_date DESC
        LIMIT 200
        """,
        conn,
        params=(query,),
    )
    conn.close()
    return df


def load_item_detail(item_id: int) -> dict:
    conn = get_conn()
    votes = pd.read_sql_query(
        "SELECT c.last_name, v.vote FROM votes v JOIN councilors c ON c.id = v.councilor_id WHERE v.agenda_item_id = ? ORDER BY v.vote, c.last_name",
        conn, params=(item_id,),
    )
    remarks = pd.read_sql_query(
        "SELECT c.last_name, r.remark_type, r.snippet FROM remarks r JOIN councilors c ON c.id = r.councilor_id WHERE r.agenda_item_id = ?",
        conn, params=(item_id,),
    )
    conn.close()
    return {"votes": votes, "remarks": remarks}


def councilor_profile(councilor_id: int) -> dict:
    conn = get_conn()
    attendance = pd.read_sql_query(
        "SELECT status, COUNT(*) n FROM attendance WHERE councilor_id = ? GROUP BY status",
        conn, params=(councilor_id,),
    )
    votes = pd.read_sql_query(
        "SELECT vote, COUNT(*) n FROM votes WHERE councilor_id = ? GROUP BY vote",
        conn, params=(councilor_id,),
    )
    sponsored = pd.read_sql_query(
        """
        SELECT m.meeting_date, ai.section, ai.committee, ai.description, ai.disposition, m.minutes_url
        FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
        WHERE ai.sponsor_councilor_id = ? ORDER BY m.meeting_date DESC
        """,
        conn, params=(councilor_id,),
    )
    dissents = pd.read_sql_query(
        """
        SELECT m.meeting_date, ai.description, m.minutes_url
        FROM votes v
        JOIN agenda_items ai ON ai.id = v.agenda_item_id
        JOIN meetings m ON m.id = ai.meeting_id
        WHERE v.councilor_id = ? AND v.vote = 'no'
        ORDER BY m.meeting_date DESC
        """,
        conn, params=(councilor_id,),
    )
    remark_committees = pd.read_sql_query(
        """
        SELECT ai.committee, COUNT(*) n
        FROM remarks r JOIN agenda_items ai ON ai.id = r.agenda_item_id
        WHERE r.councilor_id = ? AND ai.committee IS NOT NULL
        GROUP BY ai.committee ORDER BY n DESC
        """,
        conn, params=(councilor_id,),
    )
    conn.close()
    return {
        "attendance": attendance, "votes": votes, "sponsored": sponsored,
        "dissents": dissents, "remark_committees": remark_committees,
    }


MINUTES_LINK_COLUMN = {
    "minutes_url": st.column_config.LinkColumn("Minutes PDF", display_text="Open PDF"),
}


def _or(value, default):
    """pandas returns NaN (not None) for SQL NULLs read via read_sql_query,
    and NaN is truthy in plain Python -- `value or default` silently keeps
    the NaN. Use this instead wherever a NULL-able column feeds an f-string."""
    return value if pd.notna(value) else default


def run_refresh():
    steps = ["fetch_meetings.py", "fetch_pdfs.py", "extract_text.py", "parse_minutes.py"]
    progress = st.progress(0.0, text="Starting refresh...")
    for i, script in enumerate(steps):
        progress.progress(i / len(steps), text=f"Running {script}...")
        result = subprocess.run([sys.executable, script], capture_output=True, text=True)
        if result.returncode != 0:
            st.error(f"{script} failed:\n{result.stderr}")
            return
        st.text(result.stdout)
    progress.progress(1.0, text="Done")
    st.cache_data.clear()
    st.success("Refresh complete.")


# ---------------------------------------------------------------- sidebar

st.sidebar.title("Waltham City Council Tracker")
page = st.sidebar.radio("View", ["Topic Search", "Councilor Profile", "Compare Councilors", "Similarity Map", "Chat"])
st.sidebar.divider()
if st.sidebar.button("Refresh data from AgendaCenter"):
    run_refresh()

councilors = load_councilors()
active = councilors[councilors["active"] == 1]

# ---------------------------------------------------------------- pages

if page == "Topic Search":
    st.header("Search votes & statements by topic")
    query = st.text_input("Keyword (e.g. \"affordable housing\", \"Mt Walley Road\", \"zoning\")")
    if query:
        results = search_topic(query)
        st.write(f"{len(results)} matching agenda items")
        for _, row in results.iterrows():
            committee_suffix = f"/{row['committee']}" if pd.notna(row["committee"]) else ""
            with st.expander(f"{row['meeting_date']} — [{row['section']}{committee_suffix}] {row['description'][:120]}"):
                st.write(row["description"])
                st.caption(f"Disposition: {_or(row['disposition'], 'unknown')} | Vote type: {_or(row['vote_type'], 'not recorded')}")
                detail = load_item_detail(row["id"])
                if not detail["votes"].empty:
                    st.write("**Votes:**")
                    st.dataframe(detail["votes"], hide_index=True, width='stretch')
                if not detail["remarks"].empty:
                    st.write("**Remarks:**")
                    st.dataframe(detail["remarks"], hide_index=True, width='stretch')
    else:
        st.info("Enter a keyword above to search agenda items, votes, and remarks.")

elif page == "Councilor Profile":
    st.header("Councilor Profile")
    name_map = dict(zip(active["full_name"] + " (" + active["seat"] + ")", active["id"]))
    choice = st.selectbox("Councilor", list(name_map.keys()))
    cid = name_map[choice]
    profile = councilor_profile(cid)

    col1, col2, col3 = st.columns(3)
    att = profile["attendance"].set_index("status")["n"] if not profile["attendance"].empty else {}
    col1.metric("Present", int(att.get("present", 0)))
    col1.metric("Absent", int(att.get("absent", 0)))
    votes = profile["votes"].set_index("vote")["n"] if not profile["votes"].empty else {}
    col2.metric("Yes votes", int(votes.get("yes", 0)))
    col2.metric("No votes", int(votes.get("no", 0)))
    col3.metric("Recused", int(votes.get("recused", 0)))
    col3.metric("Items sponsored", len(profile["sponsored"]))

    st.subheader("Dissenting votes (voted 'no')")
    if profile["dissents"].empty:
        st.write("No recorded dissenting votes.")
    else:
        st.dataframe(profile["dissents"], hide_index=True, width='stretch', column_config=MINUTES_LINK_COLUMN)

    st.subheader("Topics spoken on, by committee")
    if profile["remark_committees"].empty:
        st.write("No recorded remarks.")
    else:
        st.bar_chart(profile["remark_committees"].set_index("committee"))

    st.subheader("Items sponsored")
    st.dataframe(profile["sponsored"], hide_index=True, width='stretch', column_config=MINUTES_LINK_COLUMN)

elif page == "Compare Councilors":
    st.header("Compare two councilors")
    name_map = dict(zip(active["full_name"], active["id"]))
    names = list(name_map.keys())
    c1, c2 = st.columns(2)
    a_name = c1.selectbox("Councilor A", names, index=0)
    b_name = c2.selectbox("Councilor B", names, index=min(1, len(names) - 1))
    a_id, b_id = name_map[a_name], name_map[b_name]

    result = load_similarity()
    sim = result["combined_similarity"]
    vote_sim = result["vote_similarity"]
    score = sim.loc[a_id, b_id] if a_id != b_id else 1.0
    vscore = vote_sim.loc[a_id, b_id] if a_id != b_id else 1.0
    m1, m2 = st.columns(2)
    m1.metric("Combined similarity", f"{score:.0%}")
    m2.metric("Roll-call vote agreement", f"{vscore:.0%}")

    conn = get_conn()
    shared = pd.read_sql_query(
        """
        SELECT m.meeting_date, ai.description, va.vote AS vote_a, vb.vote AS vote_b, m.minutes_url
        FROM votes va
        JOIN votes vb ON vb.agenda_item_id = va.agenda_item_id AND vb.councilor_id = ?
        JOIN agenda_items ai ON ai.id = va.agenda_item_id
        JOIN meetings m ON m.id = ai.meeting_id
        WHERE va.councilor_id = ? AND va.vote IN ('yes','no') AND vb.vote IN ('yes','no')
        ORDER BY m.meeting_date DESC
        """,
        conn, params=(b_id, a_id),
    )
    conn.close()
    st.subheader("Shared roll-call votes")
    if shared.empty:
        st.write("No shared roll-call votes recorded.")
    else:
        shared["agreed"] = shared["vote_a"] == shared["vote_b"]
        disagreements = shared[~shared["agreed"]]
        st.write(f"{len(shared)} shared roll calls, disagreed on {len(disagreements)}")
        if not disagreements.empty:
            st.write("**Where they disagreed:**")
            st.dataframe(disagreements.drop(columns="agreed"), hide_index=True, width='stretch', column_config=MINUTES_LINK_COLUMN)

elif page == "Similarity Map":
    st.header("Councilor Similarity Map")
    st.caption(
        "Not a left-right ‘ideology’ axis — Waltham's council votes are overwhelmingly "
        "unanimous, so this combines roll-call vote agreement with behavioral signals "
        "(who sponsors/speaks on what, committee patterns, recusals/absences) to show "
        "which councilors act most alike overall."
    )
    result = load_similarity()
    councilors_df = result["councilors"].set_index("id")
    coords = result["coords"].join(councilors_df)

    with st.expander("How to read this chart", expanded=False):
        st.markdown(
            "This is an **MDS (multidimensional scaling) plot**: each councilor is a point, "
            "placed so that the *distance* between any two points matches how different they "
            "are overall (combined vote agreement + behavioral similarity below).\n\n"
            "- **Close together** = vote together often and are active in similar ways.\n"
            "- **Far apart** = disagree more often and/or are active in different committees/topics.\n"
            "- **The X and Y axes themselves have no meaning** — MDS only guarantees that "
            "*distance* reflects dissimilarity. There's no \"liberal/conservative\" or "
            "\"issue A vs. issue B\" axis here, and the orientation is arbitrary (it can even "
            "flip or rotate if the data changes slightly). Don't read anything into which "
            "quadrant someone is in — only into how close two points are.\n"
            "- Hover over a point for that councilor's most-alike and most-different colleagues."
        )

    detail_lines = []
    for cid in coords.index:
        alike = most_similar(result, cid, n=2)
        diff = most_similar(result, cid, n=2, least=True)
        alike_txt = ", ".join(f"{r.last_name} ({r.similarity:.0%})" for r in alike.itertuples())
        diff_txt = ", ".join(f"{r.last_name} ({r.similarity:.0%})" for r in diff.itertuples())
        detail_lines.append(f"Most alike: {alike_txt}<br>Most different: {diff_txt}")
    coords = coords.copy()
    coords["detail"] = detail_lines

    fig = px.scatter(
        coords, x="x", y="y", text="last_name", color="seat",
        custom_data=["full_name", "seat", "detail"],
    )
    fig.update_traces(
        textposition="top center",
        marker=dict(size=14),
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br><br>%{customdata[2]}<extra></extra>",
    )
    fig.update_layout(
        xaxis_title="Dimension 1 (no inherent meaning — see 'How to read this chart')",
        yaxis_title="Dimension 2 (no inherent meaning)",
        height=600,
    )
    st.plotly_chart(fig, width='stretch')

    st.subheader("Pairwise agreement heatmap (roll-call votes only)")
    st.caption(
        "Unlike the map above, this one IS directly readable: each cell is the % of shared "
        "roll-call votes where that pair cast the same yes/no vote. Darker/redder = more "
        "agreement. Hover a cell to see how many shared roll calls the percentage is based on "
        "— a pair with very few shared votes is a less reliable number."
    )
    vote_sim = result["vote_similarity"]
    shared_counts = result["shared_vote_counts"]
    labels = [councilors_df.loc[i, "last_name"] for i in vote_sim.index]
    heat = go.Figure(data=go.Heatmap(
        z=vote_sim.values, x=labels, y=labels, colorscale="RdBu", zmin=0, zmax=1,
        customdata=shared_counts.values,
        hovertemplate="%{y} vs %{x}<br>Agreement: %{z:.0%}<br>Shared roll-call votes: %{customdata}<extra></extra>",
        colorbar=dict(title="Agreement", tickformat=".0%"),
    ))
    heat.update_layout(xaxis_title="Councilor", yaxis_title="Councilor", height=600)
    st.plotly_chart(heat, width='stretch')

elif page == "Chat":
    st.header("Chat")
    st.caption(
        "This is **offline keyword search over the local database, not an LLM** — no API "
        "calls, no tokens, works with no network access. It looks for a councilor's name plus "
        "a few trigger words (vote / sponsor / spoke / absent / similar), and treats the rest "
        "of your question as a search topic. It won't understand paraphrasing it doesn't "
        "recognize, so stick close to the example phrasing below if a question doesn't work."
    )
    with st.expander("Example questions", expanded=False):
        st.markdown(
            "- How did LaCava vote on affordable housing?\n"
            "- Who is most similar to Logan?\n"
            "- How often is Vidal absent?\n"
            "- What has Katz sponsored in Finance?\n"
            "- What did Durkee say about zoning?\n"
            "- Compare LaCava and Katz on Mt Walley Road"
        )

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    for entry in st.session_state.chat_history:
        with st.chat_message("user"):
            st.write(entry["question"])
        with st.chat_message("assistant"):
            st.markdown(entry["answer"])
            st.caption(f"Understood as — {entry['understood']}")
            for label, df in entry["tables"]:
                st.write(f"**{label}**")
                st.dataframe(df, hide_index=True, width='stretch')

    question = st.chat_input("Ask about a councilor, a topic, or a comparison...")
    if question:
        result = answer_question(question)
        st.session_state.chat_history.append({
            "question": question,
            "answer": result["answer"],
            "tables": result["tables"],
            "understood": result["understood"],
        })
        st.rerun()

    if st.session_state.chat_history and st.sidebar.button("Clear chat history"):
        st.session_state.chat_history = []
        st.rerun()
