"""
Tests for build_similarity.py against the fixture defined in conftest.py.
Vote-agreement and shared-vote-count values are exact (hand-computed in
conftest.py's docstring, fully traceable from the fixture data) --
combined similarity (which also folds in the behavior profile) is
asserted by ranking rather than exact float, since the behavior-profile
cosine similarity is sensitive to feature-vector construction details
that are reasonable to refactor without literally every float changing.
"""

import pytest

import build_similarity as bs
import db


def test_vote_agreement_matrix_exact_values(seeded_db):
    ids = seeded_db["councilor_ids"]
    conn = db.get_conn()
    agreement, counts = bs.vote_agreement_matrix(conn, list(ids.values()))
    conn.close()

    expected_agreement = {
        ("Anderson", "Baker"): 0.5,
        ("Anderson", "Clark"): 0.0,
        ("Anderson", "Davis"): 1.0,
        ("Baker", "Clark"): 0.5,
        ("Baker", "Davis"): 0.0,
        ("Clark", "Davis"): 0.0,
    }
    expected_counts = {
        ("Anderson", "Baker"): 2,
        ("Anderson", "Clark"): 2,
        ("Anderson", "Davis"): 1,
        ("Baker", "Clark"): 2,
        ("Baker", "Davis"): 1,
        ("Clark", "Davis"): 1,
    }
    for (a, b), expected in expected_agreement.items():
        assert agreement.loc[ids[a], ids[b]] == pytest.approx(expected), f"{a}-{b} agreement"
    for (a, b), expected in expected_counts.items():
        assert counts.loc[ids[a], ids[b]] == expected, f"{a}-{b} shared count"


def test_vote_agreement_matrix_fills_no_data_pairs_with_overall_mean(seeded_db):
    # Evans has zero recorded votes -- every Evans-X pair must fall back to
    # the overall mean, not 0% or NaN. That mean is taken over the FULL
    # 5x5 matrix *after* the diagonal has already been set to 1.0 (each
    # councilor trivially agrees with themselves), so it's not just the
    # mean of the 6 known off-diagonal pairs -- it's
    # (5 diagonal * 1.0 + 6 known pairs * 2 [symmetric]) / (25 - 8 NaN cells):
    # (5*1.0 + 2*(0.5+0.0+1.0+0.5+0.0+0.0)) / 17
    overall_mean = (5 * 1.0 + 2 * (0.5 + 0.0 + 1.0 + 0.5 + 0.0 + 0.0)) / 17
    ids = seeded_db["councilor_ids"]
    conn = db.get_conn()
    agreement, counts = bs.vote_agreement_matrix(conn, list(ids.values()))
    conn.close()

    for other in ("Anderson", "Baker", "Clark", "Davis"):
        assert agreement.loc[ids["Evans"], ids[other]] == pytest.approx(overall_mean)
        assert counts.loc[ids["Evans"], ids[other]] == 0


def test_vote_agreement_matrix_self_similarity_is_one(seeded_db):
    ids = seeded_db["councilor_ids"]
    conn = db.get_conn()
    agreement, _ = bs.vote_agreement_matrix(conn, list(ids.values()))
    conn.close()
    for cid in ids.values():
        assert agreement.loc[cid, cid] == 1.0


def test_compute_similarity_shape_and_diagonal(seeded_db):
    result = bs.compute_similarity()
    n = len(result["councilors"])
    assert n == 5
    combined = result["combined_similarity"]
    assert combined.shape == (n, n)
    assert (combined.values.diagonal() == 1.0).all()
    assert result["coords"].shape == (n, 2)


def test_most_similar_ranks_by_combined_similarity(seeded_db):
    ids = seeded_db["councilor_ids"]
    result = bs.compute_similarity()

    top = bs.most_similar(result, ids["Anderson"], n=1)
    assert top.iloc[0]["last_name"] == "Davis"  # 100% vote agreement, no data to contradict it

    bottom = bs.most_similar(result, ids["Anderson"], n=1, least=True)
    # Clark, not Evans: Clark has a real, confirmed 0% vote agreement with
    # Anderson, which outweighs Evans's mean-filled ~53% vote agreement
    # (no data -> neutral fallback, not automatically "least similar") even
    # after folding in the behavior profile.
    assert bottom.iloc[0]["last_name"] == "Clark"


def test_behavior_profile_matrix_zero_activity_councilor_is_zero_vector(seeded_db):
    ids = seeded_db["councilor_ids"]
    conn = db.get_conn()
    profile = bs.behavior_profile_matrix(conn, list(ids.values()))
    conn.close()
    assert (profile.loc[ids["Evans"]] == 0).all()
