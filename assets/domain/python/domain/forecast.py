"""
Match forecast: expected goals from Elo ratings, then an independent Poisson
goals model giving 1X2 and Over/Under 2.5 probabilities with fair decimal odds.

The two constants are calibrated against the committed match history (modern
international football, matches since 2010):
  AVG_GOALS_PER_TEAM  base goals per team in an evenly-rated match
  ELO_COEF            Elo sensitivity (how a rating gap shifts expected goals)
The Elo difference maps to expected goals, the Poisson scoreline matrix gives
the markets. Pure standard library; no model fitting at runtime.

forecast(rating_a, rating_b) is the pure model. forecast_match(team_a, team_b)
reads the two teams' Elo from the Teams table and returns the full result; the
agent's forecast tool and the REST forecast endpoint both call it, so a button
press and an agent request run identical code.
"""

import math
import os

import boto3

AVG_GOALS_PER_TEAM = 1.3638
ELO_COEF = 0.0019045
MAX_GOALS = 10  # scoreline matrix truncation; tail beyond this is negligible

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_teams = _ddb.Table(os.environ.get("TEAMS_TABLE", "Teams"))


def _poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _fair_odds(p):
    if p <= 0.0:
        return None
    return round(1.0 / p, 2)


def expected_goals(rating_a, rating_b):
    """Expected goals for A and B from their Elo ratings (neutral venue)."""
    dr = rating_a - rating_b
    lam_a = AVG_GOALS_PER_TEAM * math.exp(ELO_COEF * dr)
    lam_b = AVG_GOALS_PER_TEAM * math.exp(-ELO_COEF * dr)
    return lam_a, lam_b


def forecast(rating_a, rating_b):
    """
    1X2 and Over/Under 2.5 probabilities and fair decimal odds for a
    neutral-venue match between team A (rating_a) and team B (rating_b).
    """
    lam_a, lam_b = expected_goals(rating_a, rating_b)
    pa = [_poisson_pmf(i, lam_a) for i in range(MAX_GOALS + 1)]
    pb = [_poisson_pmf(j, lam_b) for j in range(MAX_GOALS + 1)]

    p_a = p_draw = p_b = p_over = p_under = 0.0
    total = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p = pa[i] * pb[j]
            total += p
            if i > j:
                p_a += p
            elif i == j:
                p_draw += p
            else:
                p_b += p
            if i + j >= 3:
                p_over += p
            else:
                p_under += p

    # Renormalise for the truncated tail so probabilities sum to 1.
    probs = {
        "teamA": p_a / total,
        "draw": p_draw / total,
        "teamB": p_b / total,
        "over25": p_over / total,
        "under25": p_under / total,
    }
    return {
        "expectedGoals": {"teamA": round(lam_a, 2), "teamB": round(lam_b, 2)},
        "probabilities": {k: round(v, 4) for k, v in probs.items()},
        "odds": {k: _fair_odds(v) for k, v in probs.items()},
    }


def _team_rating(team_id):
    """Return a team's Elo rating, or None if the team is not in the table."""
    resp = _teams.get_item(Key={"teamId": team_id})
    item = resp.get("Item")
    if item is None:
        return None
    return int(item["eloRating"])


def forecast_match(team_a, team_b):
    """Full forecast for two named teams, reading Elo from the Teams table.

    Returns a dict with both teams, their ratings, and the forecast markets, or
    a dict with an 'error' key if a team name is not recognised. This is the
    single implementation shared by the agent tool and the REST endpoint.
    """
    rating_a = _team_rating(team_a)
    rating_b = _team_rating(team_b)
    missing = [t for t, r in ((team_a, rating_a), (team_b, rating_b)) if r is None]
    if missing:
        return {"error": f"Unknown team name(s): {', '.join(missing)}. Use exact national team names."}

    result = forecast(rating_a, rating_b)
    return {
        "teamA": team_a,
        "teamB": team_b,
        "ratings": {"teamA": rating_a, "teamB": rating_b},
        "expectedGoals": result["expectedGoals"],
        "probabilities": result["probabilities"],
        "odds": result["odds"],
    }
