"""
build_similarity.py

Computes how similar each pair of councilors is, combining:
  1. Roll-call vote agreement -- the classic GovTrack-style signal.
  2. A behavioral profile per councilor (which committees they sponsor
     items in, which committees they speak up in, how often they're
     absent/recused) -- added because a real pass over the 2025-2026
     minutes shows most roll calls are unanimous (only ~10 of 91 had any
     dissenting vote at all), so vote agreement alone barely separates
     councilors. Behavioral signal fills that gap.

Combined into one "similarity" score per pair (0-1, higher = more alike)
and a 2D MDS projection for plotting. Both the CLI entry point below and
app.py call the same functions here, so the app always reflects
whatever's currently in the database -- there's no separate cached
artifact to go stale.
"""

import warnings

import numpy as np
import pandas as pd
from sklearn.manifold import MDS

from db import get_conn

# sklearn's MDS API is mid-transition (dissimilarity= -> metric= rename
# landing in 1.10); the current stable call below is correct for the
# installed version and just triggers a forward-looking FutureWarning.
warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn.manifold._mds")

VOTE_WEIGHT = 0.6
BEHAVIOR_WEIGHT = 0.4


def load_councilors(conn) -> pd.DataFrame:
    return pd.read_sql_query("SELECT id, last_name, full_name, seat, active FROM councilors", conn)


def vote_agreement_matrix(conn, councilor_ids: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pairwise fraction of shared roll-call items where both cast yes/no
    and agreed, plus a companion matrix of how many shared roll-call votes
    that's based on (low counts = less reliable). Pairs with no shared
    roll-call votes default to NaN agreement, later filled with the overall
    mean agreement (be neutral, not similar, about pairs that never both
    got a recorded vote)."""
    votes = pd.read_sql_query(
        """
        SELECT v.agenda_item_id, v.councilor_id, v.vote
        FROM votes v
        JOIN agenda_items ai ON ai.id = v.agenda_item_id
        WHERE ai.vote_type = 'roll_call' AND v.vote IN ('yes', 'no')
        """,
        conn,
    )
    pivot = votes.pivot(index="agenda_item_id", columns="councilor_id", values="vote")

    n = len(councilor_ids)
    values = np.full((n, n), np.nan)
    counts = np.zeros((n, n), dtype=int)
    for i, a in enumerate(councilor_ids):
        if a not in pivot.columns:
            continue
        for j in range(i + 1, n):
            b = councilor_ids[j]
            if b not in pivot.columns:
                continue
            both = pivot[[a, b]].dropna()
            if len(both) == 0:
                continue
            score = (both[a] == both[b]).mean()
            values[i, j] = score
            values[j, i] = score
            counts[i, j] = len(both)
            counts[j, i] = len(both)
    np.fill_diagonal(values, 1.0)
    overall_mean = np.nanmean(values)
    values = np.where(np.isnan(values), overall_mean, values)
    agreement = pd.DataFrame(values, index=councilor_ids, columns=councilor_ids)
    shared_counts = pd.DataFrame(counts, index=councilor_ids, columns=councilor_ids)
    return agreement, shared_counts


def behavior_profile_matrix(conn, councilor_ids: list[int]) -> pd.DataFrame:
    """One row per councilor: counts of (sponsored items, remarks, recusals,
    absences) broken out by committee, L2-normalized so raw activity level
    doesn't dominate -- this profile captures WHERE a councilor is engaged,
    not just HOW MUCH."""
    sponsor = pd.read_sql_query(
        "SELECT sponsor_councilor_id AS councilor_id, committee, COUNT(*) AS n "
        "FROM agenda_items WHERE sponsor_councilor_id IS NOT NULL GROUP BY sponsor_councilor_id, committee",
        conn,
    )
    sponsor["feature"] = "sponsor:" + sponsor["committee"].fillna("Other")

    remarks = pd.read_sql_query(
        """
        SELECT r.councilor_id, ai.committee, r.remark_type, COUNT(*) AS n
        FROM remarks r JOIN agenda_items ai ON ai.id = r.agenda_item_id
        GROUP BY r.councilor_id, ai.committee, r.remark_type
        """,
        conn,
    )
    remarks["feature"] = remarks["remark_type"] + ":" + remarks["committee"].fillna("Other")

    attendance = pd.read_sql_query(
        "SELECT councilor_id, status AS feature, COUNT(*) AS n FROM attendance GROUP BY councilor_id, status",
        conn,
    )
    attendance["feature"] = "attendance:" + attendance["feature"]

    parts = [df[["councilor_id", "feature", "n"]] for df in (sponsor, remarks, attendance) if not df.empty]
    if not parts:
        return pd.DataFrame(0, index=councilor_ids, columns=["_none"])

    long = pd.concat(parts, ignore_index=True)
    wide = long.pivot_table(index="councilor_id", columns="feature", values="n", aggfunc="sum", fill_value=0)
    wide = wide.reindex(councilor_ids, fill_value=0)

    norms = np.linalg.norm(wide.values, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return pd.DataFrame(wide.values / norms, index=wide.index, columns=wide.columns)


def behavior_similarity_matrix(profile: pd.DataFrame) -> pd.DataFrame:
    sim = profile.values @ profile.values.T  # cosine similarity, rows already L2-normalized
    return pd.DataFrame(np.clip(sim, 0, 1), index=profile.index, columns=profile.index)


def compute_similarity() -> dict:
    """Returns dict with councilors, combined similarity matrix, vote-only
    matrix, and a 2D MDS layout -- everything the app needs to render."""
    conn = get_conn()
    councilors = load_councilors(conn)
    ids = councilors["id"].tolist()

    vote_sim, shared_vote_counts = vote_agreement_matrix(conn, ids)
    profile = behavior_profile_matrix(conn, ids)
    behavior_sim = behavior_similarity_matrix(profile)

    combined_values = (VOTE_WEIGHT * vote_sim + BEHAVIOR_WEIGHT * behavior_sim).to_numpy(copy=True)
    np.fill_diagonal(combined_values, 1.0)
    combined = pd.DataFrame(combined_values, index=ids, columns=ids)

    distance = 1 - combined_values
    np.fill_diagonal(distance, 0)
    distance = (distance + distance.T) / 2  # guard against float asymmetry for MDS

    mds = MDS(n_components=2, dissimilarity="precomputed", random_state=42, normalized_stress="auto", init="random")
    coords = mds.fit_transform(distance)

    conn.close()
    return {
        "councilors": councilors,
        "combined_similarity": combined,
        "vote_similarity": vote_sim,
        "shared_vote_counts": shared_vote_counts,
        "behavior_similarity": behavior_sim,
        "coords": pd.DataFrame(coords, index=ids, columns=["x", "y"]),
    }


def most_similar(result: dict, councilor_id: int, n: int = 3, least: bool = False) -> pd.DataFrame:
    """Top-N other councilors by combined similarity to the given one (or
    bottom-N least similar if least=True). Used by both the app's chart
    hover text and the offline Q&A feature."""
    sim = result["combined_similarity"]
    councilors = result["councilors"].set_index("id")
    scores = sim.loc[councilor_id].drop(index=councilor_id).sort_values(ascending=least)
    out = councilors.loc[scores.index, ["last_name", "full_name", "seat"]].copy()
    out["similarity"] = scores.values
    return out.head(n)


def main() -> None:
    result = compute_similarity()
    councilors = result["councilors"].set_index("id")
    sim = result["combined_similarity"]
    print("Most similar councilor pairs (combined vote + behavior similarity):")
    pairs = []
    for i, a in enumerate(sim.index):
        for b in sim.index[i + 1:]:
            pairs.append((sim.loc[a, b], councilors.loc[a, "last_name"], councilors.loc[b, "last_name"]))
    pairs.sort(reverse=True)
    for score, a, b in pairs[:10]:
        print(f"  {a:20s} <-> {b:20s}  {score:.3f}")
    print("\nLeast similar pairs:")
    for score, a, b in pairs[-10:]:
        print(f"  {a:20s} <-> {b:20s}  {score:.3f}")


if __name__ == "__main__":
    main()
