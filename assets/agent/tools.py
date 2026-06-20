"""
Tools for the World Cup agent specialists.

Each tool is a plain function turned into an agent tool by the @tool decorator;
its type hints and docstring become the spec the model sees, so the wording is
deliberate. Tools read configuration (table names, knowledge base id, model arn)
from the environment so nothing is account-specific. The forecast and betting
logic live in the shared domain package so the agent and the REST endpoint run
identical code.

The betting tools act on a wallet, so they must act on the *right* wallet. The
verified caller is injected into a contextvar by the runtime entrypoint before
the graph runs (see set_current_user) and read here; it is never taken from the
model, so a user cannot bet from someone else's wallet.
"""

import contextvars
import logging
import os

import boto3
from botocore.exceptions import ClientError
from strands import tool

from domain import betting
from domain import forecast as fc
from domain import matches
from domain import wallet

logger = logging.getLogger("worldcup.tools")

_REGION = os.environ.get("AWS_REGION", "us-east-1")

_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=_REGION)

# The verified caller for the current invocation. Set by the runtime entrypoint,
# read by the betting tools, default None so a missing identity is an explicit error
# rather than a wrong wallet.
_current_user = contextvars.ContextVar("current_user", default=None)


def set_current_user(user_id):
    """Record the verified caller for this invocation so the betting tools act on the
    right wallet. Set out of band by the entrypoint, never from model output."""
    _current_user.set(user_id)


@tool
def query_team_knowledge(question: str) -> dict:
    """Answer a question about World Cup teams, their history, or head-to-head
    records using the football knowledge base. Use this for factual questions
    such as a team's World Cup record, past results, or how two nations have
    fared against each other historically.

    Args:
        question: The natural-language question to answer.

    Returns:
        A dict with the grounded answer text and the source documents used.
    """
    try:
        resp = _agent_runtime.retrieve_and_generate(
            input={"text": question},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": os.environ["KNOWLEDGE_BASE_ID"],
                    "modelArn": os.environ["KB_MODEL_ARN"],
                },
            },
        )
    except ClientError as exc:
        # Do not let a knowledge base failure vanish into a generic fallback.
        # Log the full traceback to CloudWatch and return the error so it is
        # visible in the invoke response too. The echoed config confirms which
        # knowledge base id and model arn the call actually used.
        logger.exception("query_team_knowledge failed")
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "knowledgeBaseId": os.environ.get("KNOWLEDGE_BASE_ID"),
            "modelArn": os.environ.get("KB_MODEL_ARN"),
        }

    sources = []
    for citation in resp.get("citations", []):
        for ref in citation.get("retrievedReferences", []):
            uri = ref.get("location", {}).get("s3Location", {}).get("uri")
            if uri and uri not in sources:
                sources.append(uri)
    return {"answer": resp["output"]["text"], "sources": sources}


@tool
def get_match_forecast(team_a: str, team_b: str) -> dict:
    """Forecast a match between two World Cup teams. Returns win/draw/loss
    (1X2) probabilities, Over/Under 2.5 goals probabilities, fair decimal odds,
    and the expected goals for each side. The model is based on the teams' Elo
    ratings and a Poisson goals model. Use the exact national team names.

    Args:
        team_a: First team name (for example "Brazil").
        team_b: Second team name (for example "France").

    Returns:
        A dict with the two teams, their ratings, and the forecast markets, or
        an error if a team name is not recognised.
    """
    return fc.forecast_match(team_a, team_b)


# --- betting -----------------------------------------------------------------

class _PickError(Exception):
    """The match or the pick could not be resolved; the message is for the user."""


def _resolve_selection(match, pick):
    """Map a natural pick to (market, selection) for this fixture. A team name becomes a
    1X2 HOME/AWAY by which side that team is; "draw", "over 2.5", "under 2.5" map to the
    draw and Over/Under markets. Returns (None, None) if the pick is not understood."""
    p = pick.strip().casefold()
    home = match.get("teamHome", "").strip().casefold()
    away = match.get("teamAway", "").strip().casefold()
    if p == home:
        return "1X2", "HOME"
    if p == away:
        return "1X2", "AWAY"
    if p in ("draw", "tie", "x"):
        return "1X2", "DRAW"
    if p in ("over", "over 2.5", "o2.5", "over2.5"):
        return "OU25", "OVER"
    if p in ("under", "under 2.5", "u2.5", "under2.5"):
        return "OU25", "UNDER"
    return None, None

def _resolve(team_a, team_b, pick):
    """Resolve a conversational bet to (match, market, selection). Raises _PickError with
    a user-readable message when the fixture or the pick cannot be resolved."""
    match = matches.find_open_match(team_a, team_b)
    if match is None:
        raise _PickError(
            f"No scheduled match found for {team_a} vs {team_b}. Use exact national team "
            "names, and note only matches that have not kicked off can be bet on."
        )
    market, selection = _resolve_selection(match, pick)
    if market is None:
        raise _PickError(
            f"I could not read the pick '{pick}'. Bet on a team to win, or 'draw', "
            "'over 2.5', or 'under 2.5'."
        )
    return match, market, selection


@tool
def quote_bet(team_a: str, team_b: str, pick: str, stake: int) -> dict:
    """Preview a fake-coin bet on a scheduled World Cup match WITHOUT placing it. No coins
    move. Use this first, to show the odds and potential return so the user can confirm
    before the bet is placed.

    Args:
        team_a: One team's exact national name (for example "Brazil").
        team_b: The other team's exact national name (for example "France").
        pick: What to bet on: a team's name to win, "draw", "over 2.5", or "under 2.5".
        stake: Whole number of fake coins to stake.

    Returns:
        The matched teams, the priced decimal odds, the stake, the potential return, and
        the user's current and resulting balance; or an "error" message to relay.
    """
    user_id = _current_user.get()
    try:
        match, market, selection = _resolve(team_a, team_b, pick)
        quote = betting.quote_bet(match["matchId"], market, selection, stake)
    except _PickError as exc:
        return {"error": str(exc)}
    except (betting.MatchNotFound, betting.MatchNotOpen, betting.InvalidBet) as exc:
        return {"error": str(exc)}
    result = {
        "teamHome": quote["teamHome"],
        "teamAway": quote["teamAway"],
        "pick": pick,
        "stake": stake,
        "odds": quote["odds"],
        "potentialReturn": quote["potentialReturn"],
    }
    balance = wallet.get_balance(user_id) if user_id else None
    if balance is not None:
        result["balance"] = balance
        result["balanceAfter"] = balance - stake
    return result


@tool
def place_bet(team_a: str, team_b: str, pick: str, stake: int) -> dict:
    """Place a fake-coin bet on a scheduled World Cup match. This MOVES coins: it debits
    the stake from the user's wallet and records the bet, atomically. Only call this after
    the user has seen a quote and explicitly confirmed. No real money is ever involved.

    Args:
        team_a: One team's exact national name.
        team_b: The other team's exact national name.
        pick: What to bet on: a team's name to win, "draw", "over 2.5", or "under 2.5".
        stake: Whole number of fake coins to stake.

    Returns:
        The placed bet's id, the teams, stake, odds, potential return, and the new
        balance; or an "error" message to relay.
    """
    user_id = _current_user.get()
    if not user_id:
        return {"error": "No signed-in user, so I cannot place a bet."}
    try:
        match, market, selection = _resolve(team_a, team_b, pick)
        bet = betting.place_bet(user_id, match["matchId"], market, selection, stake)
    except _PickError as exc:
        return {"error": str(exc)}
    except betting.InsufficientFunds as exc:
        return {"error": f"Not enough coins: {exc}"}
    except (betting.MatchNotFound, betting.MatchNotOpen, betting.InvalidBet) as exc:
        return {"error": str(exc)}
    odds = float(bet["oddsSnapshot"])
    return {
        "betId": bet["betId"],
        "teamHome": match["teamHome"],
        "teamAway": match["teamAway"],
        "pick": pick,
        "stake": bet["stake"],
        "odds": odds,
        "potentialReturn": round(bet["stake"] * odds, 2),
        "status": bet["status"],
        "balance": wallet.get_balance(user_id),
    }