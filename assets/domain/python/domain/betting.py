"""
Place and list bets.

Placing a bet is the one money-moving operation. It validates the match is open,
prices the bet from the live forecast (the same forecast_match the agent and the
forecast endpoint use), then debits the stake and records the bet as a single
DynamoDB transaction. The wallet and the bet can never disagree: either both the
debit and the bet write happen or neither does. The wallet debit carries a
balance condition, so a bet that cannot be covered cancels the whole transaction.
"""

import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from . import forecast, matches
from .wallet import InsufficientFunds

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_BETS_TABLE = os.environ.get("BETS_TABLE", "Bets")
_WALLETS_TABLE = os.environ.get("WALLETS_TABLE", "Wallets")

_client = boto3.client("dynamodb", region_name=_REGION)
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_bets = _ddb.Table(_BETS_TABLE)
_ser = TypeSerializer()

# market -> selection -> the key in the forecast odds dict
_ODDS_KEY = {
    "1X2": {"HOME": "teamA", "DRAW": "draw", "AWAY": "teamB"},
    "OU25": {"OVER": "over25", "UNDER": "under25"},
}


class MatchNotFound(Exception):
    """No match with that id."""


class MatchNotOpen(Exception):
    """The match is not accepting bets (not scheduled, or already kicked off)."""


class InvalidBet(Exception):
    """The market, selection, or stake is not valid."""


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _b32(value, length):
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def _new_bet_id():
    """A ULID: 48-bit millisecond timestamp then 80 bits of randomness, Crockford
    base32. Lexicographic order is chronological, so a user's bets sort by time."""
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    return _b32(ts, 10) + _b32(rand, 16)


def _serialize(item):
    return {k: _ser.serialize(v) for k, v in item.items()}


def _kickoff_in_future(kickoff):
    dt = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
    return dt > datetime.now(timezone.utc)


def _price_bet(match_id, bet_type, selection, stake):
    """Validate the stake and market, confirm the match is open, and price the selection
    from the live forecast. Returns (match, odds). Shared by quote_bet and place_bet so a
    preview and an actual placement validate and price identically. Raises MatchNotFound,
    MatchNotOpen, or InvalidBet."""
    if not isinstance(stake, int) or isinstance(stake, bool) or stake <= 0:
        raise InvalidBet("stake must be a positive whole number of coins")
    odds_key = _ODDS_KEY.get(bet_type, {}).get(selection)
    if odds_key is None:
        raise InvalidBet(f"unknown market or selection: {bet_type}/{selection}")

    match = matches.get_match(match_id)
    if match is None:
        raise MatchNotFound(match_id)
    kickoff = match.get("kickoff")
    if match.get("status") != "SCHEDULED" or not kickoff or not _kickoff_in_future(kickoff):
        raise MatchNotOpen(f"{match_id} is not open for betting")

    quote = forecast.forecast_match(match["teamHome"], match["teamAway"])
    if "error" in quote:
        raise InvalidBet(quote["error"])
    odds = quote["odds"][odds_key]
    if odds is None:
        raise InvalidBet("no price available for that selection")
    return match, odds


def quote_bet(match_id, bet_type, selection, stake):
    """Price a bet without placing it: the same validation and live pricing as place_bet,
    returning the teams, the odds, and the potential return, but touching no wallet or
    bet. Used to preview a bet for confirmation before it is placed."""
    match, odds = _price_bet(match_id, bet_type, selection, stake)
    return {
        "matchId": match_id,
        "teamHome": match["teamHome"],
        "teamAway": match["teamAway"],
        "betType": bet_type,
        "selection": selection,
        "stake": stake,
        "odds": odds,
        "potentialReturn": round(stake * odds, 2),
    }


def place_bet(user_id, match_id, bet_type, selection, stake):
    """Place a bet for user_id on match_id.

    bet_type is "1X2" (selection HOME/DRAW/AWAY) or "OU25" (selection OVER/UNDER).
    stake is a positive whole number of coins. Returns the stored bet. Raises
    MatchNotFound, MatchNotOpen, InvalidBet, or InsufficientFunds.
    """
    match, odds = _price_bet(match_id, bet_type, selection, stake)

    now = _now_iso()
    bet = {
        "userId": user_id,
        "betId": _new_bet_id(),
        "matchId": match_id,
        "betType": bet_type,
        "selection": selection,
        "stake": stake,
        "oddsSnapshot": Decimal(str(odds)),
        "status": "PENDING",
        "placedAt": now,
    }

    try:
        _client.transact_write_items(TransactItems=[
            {"Update": {
                "TableName": _WALLETS_TABLE,
                "Key": _serialize({"userId": user_id}),
                "UpdateExpression": "SET balance = balance - :amt, updatedAt = :now",
                "ConditionExpression": "attribute_exists(userId) AND balance >= :amt",
                "ExpressionAttributeValues": _serialize({":amt": stake, ":now": now}),
            }},
            {"Put": {
                "TableName": _BETS_TABLE,
                "Item": _serialize(bet),
                "ConditionExpression": "attribute_not_exists(betId)",
            }},
        ])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "TransactionCanceledException":
            reasons = exc.response.get("CancellationReasons", [])
            if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
                raise InsufficientFunds(f"balance does not cover {stake}")
        raise
    return bet


def list_bets(user_id):
    """A user's bets, newest first."""
    items = []
    start_key = None
    while True:
        params = {
            "KeyConditionExpression": Key("userId").eq(user_id),
            "ScanIndexForward": False,
        }
        if start_key:
            params["ExclusiveStartKey"] = start_key
        resp = _bets.query(**params)
        items.extend(resp.get("Items", []))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    return items