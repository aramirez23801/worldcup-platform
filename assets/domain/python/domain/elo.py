"""Elo rating maintenance: update team ratings from match results.

The forecaster reads each team's stored Elo to predict matches. As the knockout
bracket plays out we update the two teams' ratings from each result, so later-round
forecasts reflect how the tournament has actually gone.

The update follows the World Football Elo Ratings convention our seed ratings come
from: R' = R + K * G * (W - We), where We is the expected result from the rating gap,
W is the actual result (1 win, 0.5 draw, 0 loss), G scales with the goal margin, and K
weights the competition (World Cup). It is zero-sum: the winner gains exactly what the
loser drops. Knockout matches level after regulation (decided by a shootout) count as
draws here; the score we store and read is the regulation score, so the penalty winner
is never needed.

updated_elo(...) is the pure formula. apply_match_elo(...) reads both teams' current
ratings from the Teams table, applies the update, and writes both back.
"""

import os

import boto3

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_teams = _ddb.Table(os.environ.get("TEAMS_TABLE", "Teams"))

_K_WORLD_CUP = 60  # World Football Elo Ratings weight for World Cup matches


def _goal_difference_multiplier(margin):
    if margin <= 1:
        return 1.0
    if margin == 2:
        return 1.5
    return (11.0 + margin) / 8.0


def _expected(rating_for, rating_against):
    return 1.0 / (10.0 ** ((rating_against - rating_for) / 400.0) + 1.0)


def updated_elo(rating_home, rating_away, score_home, score_away, k=_K_WORLD_CUP):
    """New (home, away) integer ratings after a match. The delta is rounded once and
    applied symmetrically, so the result is exactly zero-sum."""
    if score_home > score_away:
        result_home = 1.0
    elif score_home == score_away:
        result_home = 0.5
    else:
        result_home = 0.0
    expected_home = _expected(rating_home, rating_away)
    multiplier = _goal_difference_multiplier(abs(score_home - score_away))
    delta = round(k * multiplier * (result_home - expected_home))
    return rating_home + delta, rating_away - delta


def apply_match_elo(team_home, team_away, score_home, score_away):
    """Read both teams' Elo from the Teams table, apply the result, write both back.
    Returns (new_home, new_away), or None if either team is missing from the table."""
    home_item = _teams.get_item(Key={"teamId": team_home}).get("Item")
    away_item = _teams.get_item(Key={"teamId": team_away}).get("Item")
    if home_item is None or away_item is None:
        return None
    rating_home = int(home_item["eloRating"])
    rating_away = int(away_item["eloRating"])
    new_home, new_away = updated_elo(rating_home, rating_away, score_home, score_away)
    _teams.update_item(
        Key={"teamId": team_home},
        UpdateExpression="SET eloRating = :r",
        ExpressionAttributeValues={":r": new_home},
    )
    _teams.update_item(
        Key={"teamId": team_away},
        UpdateExpression="SET eloRating = :r",
        ExpressionAttributeValues={":r": new_away},
    )
    return new_home, new_away