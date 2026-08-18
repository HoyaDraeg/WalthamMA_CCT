"""
qa.py

Offline "chat" over the tracked council data. This is deliberately NOT an
LLM -- no API calls, no tokens, works with no network access. It's a
keyword/pattern matcher: it looks for a councilor's name, a small set of
intent keywords (vote / attendance / sponsor / remarks / similarity), and
treats whatever words are left over as a full-text-search topic against
the same SQLite database and FTS5 index the rest of the app uses. That
means answers are only as good as simple keyword matching gets -- it will
miss paraphrases and synonyms it doesn't know about, and app.py's Chat
page says so up front rather than implying it's a real conversational AI.
"""

import re

import pandas as pd

from build_similarity import compute_similarity, most_similar
from db import get_conn
from parse_minutes import COMMITTEES

STOPWORDS = {
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "been", "by",
    "did", "do", "does", "for", "from", "had", "has", "have", "how", "in",
    "is", "it", "its", "of", "on", "or", "over", "that", "the", "their",
    "there", "they", "this", "to", "was", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "councillor", "councilor",
    "please", "tell", "me", "show", "find", "know", "want", "give",
    "most", "many", "much", "often", "get", "can", "you", "your",
    "his", "her", "him", "she", "he", "them", "all", "some",
    "usually", "typically", "generally", "normally", "always", "never",
    "recently", "currently", "still", "also", "just", "really",
}

INTENT_KEYWORDS = {
    "similarity": {"similar", "alike", "compare", "comparison", "agree", "agreement", "differ", "different", "disagree"},
    "attendance": {"absent", "attendance", "present", "miss", "missed", "recuse", "recused", "recusal"},
    "sponsor": {"sponsor", "sponsored", "sponsors", "moved", "move", "introduce", "introduced"},
    "remarks": {"say", "said", "spoke", "speak", "speaks", "statement", "statements", "remark", "remarks", "comment", "commented"},
    "vote": {"vote", "voted", "voting", "votes", "roll", "call"},
}


def _fts_sanitize(text: str) -> str | None:
    """Extract plain word tokens so arbitrary punctuation in a user
    question can't break FTS5's query syntax; bare terms are ANDed
    together by FTS5 by default."""
    words = re.findall(r"[a-zA-Z0-9]+", text)
    words = [w for w in words if len(w) > 2]
    return " ".join(words) if words else None


def find_councilors(question: str, councilors_df: pd.DataFrame) -> list[int]:
    ql = question.lower()
    matches = []
    for _, row in councilors_df.iterrows():
        last = row["last_name"].lower()
        first = row["full_name"].split()[0].lower()
        if re.search(rf"\b{re.escape(last)}\b", ql) or (len(first) > 2 and re.search(rf"\b{re.escape(first)}\b", ql)):
            matches.append(row["id"])
    seen = set()
    ordered = []
    for cid in sorted(matches, key=lambda c: ql.find(councilors_df.loc[councilors_df["id"] == c, "last_name"].iloc[0].lower())):
        if cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    return ordered


def find_intents(question: str) -> list[str]:
    words = set(re.findall(r"[a-z']+", question.lower()))
    return [intent for intent, kws in INTENT_KEYWORDS.items() if words & kws]


def find_committee(question: str) -> str | None:
    ql = question.lower()
    for c in COMMITTEES:
        if c.lower() in ql:
            return c
    return None


def extract_topic(question: str, councilors_df: pd.DataFrame, matched_ids: list[int], committee: str | None) -> str | None:
    words = re.findall(r"[a-z']+", question.lower())
    exclude = set(STOPWORDS)
    for kws in INTENT_KEYWORDS.values():
        exclude |= kws
    for cid in matched_ids:
        row = councilors_df.loc[councilors_df["id"] == cid].iloc[0]
        exclude.add(row["last_name"].lower())
        exclude.add(row["full_name"].split()[0].lower())
    if committee:
        exclude |= set(committee.lower().split())
    remaining = [w for w in words if w not in exclude and len(w) > 2]
    return " ".join(remaining) if remaining else None


def _name(councilors_df: pd.DataFrame, cid: int) -> str:
    return councilors_df.loc[councilors_df["id"] == cid, "full_name"].iloc[0]


def _seat(councilors_df: pd.DataFrame, cid: int) -> str:
    return councilors_df.loc[councilors_df["id"] == cid, "seat"].iloc[0]


def answer_question(question: str) -> dict:
    """Returns {"answer": str, "tables": [(label, DataFrame), ...],
    "understood": str} -- "understood" is a one-line trace of what got
    detected (councilor/intent/topic), shown in the UI so a wrong-looking
    answer is debuggable instead of a black box."""
    conn = get_conn()
    councilors_df = pd.read_sql_query("SELECT * FROM councilors", conn)
    matched_ids = find_councilors(question, councilors_df)
    intents = find_intents(question)
    committee = find_committee(question)
    topic = extract_topic(question, councilors_df, matched_ids, committee)
    fts_topic = _fts_sanitize(topic) if topic else None

    understood = (
        f"councilor(s): {[_name(councilors_df, c) for c in matched_ids] or 'none'} | "
        f"intent: {intents or 'none'} | committee: {committee or 'none'} | topic: {fts_topic or 'none'}"
    )
    tables: list[tuple[str, pd.DataFrame]] = []

    # ---------------------------------------------------- two councilors
    if len(matched_ids) >= 2:
        a, b = matched_ids[0], matched_ids[1]
        result = compute_similarity()
        combined = result["combined_similarity"].loc[a, b]
        vote_sim = result["vote_similarity"].loc[a, b]
        shared = int(result["shared_vote_counts"].loc[a, b])
        answer = (
            f"**{_name(councilors_df, a)}** and **{_name(councilors_df, b)}**: "
            f"{combined:.0%} combined similarity, {vote_sim:.0%} roll-call vote agreement "
            f"over {shared} shared roll-call votes."
        )

        sql = """
            SELECT m.meeting_date, ai.description, va.vote AS vote_a, vb.vote AS vote_b, m.minutes_url
            FROM votes va
            JOIN votes vb ON vb.agenda_item_id = va.agenda_item_id AND vb.councilor_id = ?
            JOIN agenda_items ai ON ai.id = va.agenda_item_id
            JOIN meetings m ON m.id = ai.meeting_id
            WHERE va.councilor_id = ? AND va.vote IN ('yes','no') AND vb.vote IN ('yes','no')
        """
        params: list = [b, a]
        if fts_topic:
            sql += " AND ai.id IN (SELECT rowid FROM agenda_items_fts WHERE agenda_items_fts MATCH ?)"
            params.append(fts_topic)
        sql += " ORDER BY m.meeting_date DESC"
        shared_df = pd.read_sql_query(sql, conn, params=params)
        if not shared_df.empty:
            shared_df["agreed"] = shared_df["vote_a"] == shared_df["vote_b"]
            disagreements = shared_df[~shared_df["agreed"]].drop(columns="agreed")
            if fts_topic:
                answer += f'\n\nOn items matching "{fts_topic}": {len(shared_df)} shared votes, disagreed on {len(disagreements)}.'
            if not disagreements.empty:
                tables.append(("Where they disagreed", disagreements))
        elif fts_topic:
            answer += f'\n\nNo shared roll-call votes found matching "{fts_topic}".'
        conn.close()
        return {"answer": answer, "tables": tables, "understood": understood}

    # ----------------------------------------------------- one councilor
    if len(matched_ids) == 1:
        cid = matched_ids[0]
        name = _name(councilors_df, cid)

        if "similarity" in intents:
            result = compute_similarity()
            alike = most_similar(result, cid, n=5)
            diff = most_similar(result, cid, n=5, least=True)
            alike_txt = ", ".join(f"{r.last_name} ({r.similarity:.0%})" for r in alike.itertuples())
            diff_txt = ", ".join(f"{r.last_name} ({r.similarity:.0%})" for r in diff.itertuples())
            answer = f"**{name}** is most similar to {alike_txt}.\n\nLeast similar to {diff_txt}."
            conn.close()
            return {"answer": answer, "tables": tables, "understood": understood}

        if "attendance" in intents:
            att = pd.read_sql_query("SELECT status, COUNT(*) n FROM attendance WHERE councilor_id=? GROUP BY status", conn, params=(cid,))
            votes_tally = pd.read_sql_query("SELECT vote, COUNT(*) n FROM votes WHERE councilor_id=? GROUP BY vote", conn, params=(cid,))
            att_map = dict(zip(att["status"], att["n"]))
            vote_map = dict(zip(votes_tally["vote"], votes_tally["n"]))
            answer = (
                f"**{name}**: present at {int(att_map.get('present', 0))} meetings, "
                f"absent from {int(att_map.get('absent', 0))}. "
                f"Recused from {int(vote_map.get('recused', 0))} recorded roll-call votes."
            )
            conn.close()
            return {"answer": answer, "tables": tables, "understood": understood}

        if "sponsor" in intents:
            sql = (
                "SELECT m.meeting_date, ai.section, ai.committee, ai.description, ai.disposition, m.minutes_url "
                "FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id WHERE ai.sponsor_councilor_id = ?"
            )
            params = [cid]
            if fts_topic:
                sql += " AND ai.id IN (SELECT rowid FROM agenda_items_fts WHERE agenda_items_fts MATCH ?)"
                params.append(fts_topic)
            if committee:
                sql += " AND ai.committee = ?"
                params.append(committee)
            sql += " ORDER BY m.meeting_date DESC"
            df = pd.read_sql_query(sql, conn, params=params)
            bits = [b for b in [f'matching "{fts_topic}"' if fts_topic else None, f"in {committee}" if committee else None] if b]
            answer = f"**{name}** sponsored/moved {len(df)} item(s)" + (" " + " ".join(bits) if bits else "") + "."
            if not df.empty:
                if not committee:
                    top = df["committee"].value_counts().head(3)
                    if not top.empty:
                        answer += " Most active in: " + ", ".join(f"{c} ({n})" for c, n in top.items()) + "."
                tables.append(("Sponsored items", df.head(30)))
            conn.close()
            return {"answer": answer, "tables": tables, "understood": understood}

        if "remarks" in intents:
            sql = (
                "SELECT m.meeting_date, ai.committee, ai.description, r.remark_type, r.snippet, m.minutes_url "
                "FROM remarks r JOIN agenda_items ai ON ai.id = r.agenda_item_id JOIN meetings m ON m.id = ai.meeting_id "
                "WHERE r.councilor_id = ?"
            )
            params = [cid]
            if fts_topic:
                sql += " AND ai.id IN (SELECT rowid FROM agenda_items_fts WHERE agenda_items_fts MATCH ?)"
                params.append(fts_topic)
            if committee:
                sql += " AND ai.committee = ?"
                params.append(committee)
            sql += " ORDER BY m.meeting_date DESC LIMIT 30"
            df = pd.read_sql_query(sql, conn, params=params)
            bits = [b for b in [f'on "{fts_topic}"' if fts_topic else None, f"in {committee}" if committee else None] if b]
            answer = f"Found {len(df)} recorded remark(s) by **{name}**" + (" " + " ".join(bits) if bits else "") + "."
            if not df.empty:
                tables.append(("Remarks", df))
            conn.close()
            return {"answer": answer, "tables": tables, "understood": understood}

        if "vote" in intents and (fts_topic or committee):
            sql = """
                SELECT m.meeting_date, ai.description, v.vote, ai.disposition, m.minutes_url
                FROM agenda_items ai
                JOIN meetings m ON m.id = ai.meeting_id
                LEFT JOIN votes v ON v.agenda_item_id = ai.id AND v.councilor_id = ?
                WHERE 1=1
            """
            params = [cid]
            if fts_topic:
                sql += " AND ai.id IN (SELECT rowid FROM agenda_items_fts WHERE agenda_items_fts MATCH ?)"
                params.append(fts_topic)
            if committee:
                sql += " AND ai.committee = ?"
                params.append(committee)
            sql += " ORDER BY m.meeting_date DESC"
            df = pd.read_sql_query(sql, conn, params=params)
            bits = [b for b in [f'matching "{fts_topic}"' if fts_topic else None, f"in {committee}" if committee else None] if b]
            answer = (
                f"Found {len(df)} item(s) " + " ".join(bits) + f" — {name}'s recorded vote is shown "
                "below (blank = no individual vote recorded for that item, e.g. it passed by voice vote)."
            )
            tables.append(("Matching items", df))
            conn.close()
            return {"answer": answer, "tables": tables, "understood": understood}

        # vote intent with no topic, or no intent detected at all -> profile summary
        att = pd.read_sql_query("SELECT status, COUNT(*) n FROM attendance WHERE councilor_id=? GROUP BY status", conn, params=(cid,))
        votes_tally = pd.read_sql_query("SELECT vote, COUNT(*) n FROM votes WHERE councilor_id=? GROUP BY vote", conn, params=(cid,))
        sponsored_n = conn.execute("SELECT COUNT(*) FROM agenda_items WHERE sponsor_councilor_id=?", (cid,)).fetchone()[0]
        dissents = pd.read_sql_query(
            """
            SELECT m.meeting_date, ai.description, m.minutes_url
            FROM votes v JOIN agenda_items ai ON ai.id = v.agenda_item_id JOIN meetings m ON m.id = ai.meeting_id
            WHERE v.councilor_id = ? AND v.vote = 'no' ORDER BY m.meeting_date DESC
            """,
            conn, params=(cid,),
        )
        att_map = dict(zip(att["status"], att["n"]))
        vote_map = dict(zip(votes_tally["vote"], votes_tally["n"]))
        answer = (
            f"**{name}** ({_seat(councilors_df, cid)}): "
            f"{int(vote_map.get('yes', 0))} yes votes, {int(vote_map.get('no', 0))} no votes, "
            f"{int(vote_map.get('recused', 0))} recusals on recorded roll calls; "
            f"present {int(att_map.get('present', 0))}, absent {int(att_map.get('absent', 0))} meetings; "
            f"sponsored {sponsored_n} items."
        )
        if fts_topic:
            answer += f' (No specific "{fts_topic}" question type recognized — showing overall profile instead; try adding "vote", "sponsor", "spoke", "absent", or "similar" to be specific.)'
        if not dissents.empty:
            tables.append(("Dissenting ('no') votes", dissents))
        conn.close()
        return {"answer": answer, "tables": tables, "understood": understood}

    # -------------------------------------------------- no councilor named
    if committee or fts_topic:
        sql = (
            "SELECT m.meeting_date, ai.section, ai.committee, ai.description, ai.disposition, m.minutes_url "
            "FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id WHERE 1=1"
        )
        params = []
        if fts_topic:
            sql += " AND ai.id IN (SELECT rowid FROM agenda_items_fts WHERE agenda_items_fts MATCH ?)"
            params.append(fts_topic)
        if committee:
            sql += " AND ai.committee = ?"
            params.append(committee)
        sql += " ORDER BY m.meeting_date DESC LIMIT 30"
        df = pd.read_sql_query(sql, conn, params=params)
        bits = []
        if fts_topic:
            bits.append(f'matching "{fts_topic}"')
        if committee:
            bits.append(f"from the {committee} committee")
        answer = f"Found {len(df)} item(s) " + " ".join(bits) + ". Mention a councilor's name to see their vote/stance on these."
        if not df.empty:
            tables.append(("Matching items", df))
        conn.close()
        return {"answer": answer, "tables": tables, "understood": understood}

    conn.close()
    return {
        "answer": (
            "I couldn't find a councilor name or a clear topic in that question. This is a "
            "keyword search, not a real conversational AI, so exact names and a few trigger "
            "words help. Try things like:\n\n"
            "- \"How did LaCava vote on affordable housing?\"\n"
            "- \"Who is most similar to Logan?\"\n"
            "- \"How often is Vidal absent?\"\n"
            "- \"What has Katz sponsored in Finance?\"\n"
            "- \"What did Durkee say about zoning?\"\n"
            "- \"Compare LaCava and Katz on Mt Walley Road\""
        ),
        "tables": [],
        "understood": understood,
    }
