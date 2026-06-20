"""Realized-profit leaderboard.

Ranks players by the money their settled bets have made: a won bet adds its winnings
above the stake, a lost bet subtracts the stake. Pending bets do not count until they
settle, so the board reflects only decided outcomes. The aggregator (driven by the Bets
stream) calls record_settlement once for each bet that transitions to WON or LOST; the
read side calls standings.

Profit is defined to equal the change the bet made to the player's wallet, so the board
and the wallet never disagree: a won bet's payout is the whole-coin amount settlement
credits, and profit is that payout minus the stake.
"""

import os
from decimal import Decimal, ROUND_HALF_UP

import boto3
from boto3.dynamodb.conditions import Key

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_LEADERBOARD_TABLE = os.environ["LEADERBOARD_TABLE"]

# The whole-tournament board. The table's pk also allows per-match boards
# ("LB#MATCH#<matchId>") later; this phase ranks the tournament.
_TOURNAMENT = "LB#TOURNAMENT"

_ddb = boto3.resource("dynamodb", region_name=_REGION)
_table = _ddb.Table(_LEADERBOARD_TABLE)


def profit_delta(status, stake, odds):
    """Realized profit from one settled bet, in whole coins. A win returns the whole-coin
    payout (stake * odds, rounded) minus the stake; a loss returns minus the stake; any
    other status is zero. Mirrors settlement's payout rounding so the board equals the
    wallet."""
    stake = Decimal(str(stake))
    if status == "WON":
        payout = (stake * Decimal(str(odds))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return payout - stake
    if status == "LOST":
        return -stake
    return Decimal(0)


def record_settlement(user_id, status, stake, odds):
    """Apply one settled bet to a user's tournament standing: add its profit delta and
    bump the win or loss count, atomically (ADD composes under concurrent settlements).
    A non-settlement status is ignored. Returns the user's new profit total, or None if
    nothing was recorded."""
    if status not in ("WON", "LOST"):
        return None
    delta = profit_delta(status, stake, odds)
    win_inc = 1 if status == "WON" else 0
    loss_inc = 1 if status == "LOST" else 0
    resp = _table.update_item(
        Key={"pk": _TOURNAMENT, "userId": user_id},
        UpdateExpression="ADD profit :d, wins :w, losses :l",
        ExpressionAttributeValues={":d": delta, ":w": win_inc, ":l": loss_inc},
        ReturnValues="UPDATED_NEW",
    )
    return resp["Attributes"]["profit"]


_MAX_NAME_LEN = 20


def set_display_name(user_id, name):
    """Set this user's leaderboard display name. Upserts the user's tournament row, so it works before they have settled any bet (profit/wins/losses then default to 0 on read). Validates server-side: keeps only printable characters, trims surrounding whitespace, requires non-empty, and caps length. Returns the stored name, or None if the name is empty after cleaning. Only ever writes displayName, so it never disturbs the profit/wins/losses the aggregator maintains."""
    if name is None:
        return None
    cleaned = "".join(ch for ch in str(name) if ch.isprintable()).strip()
    if not cleaned:
        return None
    cleaned = cleaned[:_MAX_NAME_LEN]
    _table.update_item(
        Key={"pk": _TOURNAMENT, "userId": user_id},
        UpdateExpression="SET displayName = :n",
        ExpressionAttributeValues={":n": cleaned},
    )
    return cleaned


def standings(limit=None):
    """The tournament leaderboard, players ordered by realized profit, highest first.
    Reads the single partition and sorts in memory, which suits this tournament's player count. Returns a list of {userId, profit, wins, losses}, each also carrying displayName when the player has set one."""
    rows = []
    start_key = None
    while True:
        params = {"KeyConditionExpression": Key("pk").eq(_TOURNAMENT)}
        if start_key:
            params["ExclusiveStartKey"] = start_key
        resp = _table.query(**params)
        rows.extend(resp.get("Items", []))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    rows.sort(key=lambda r: r.get("profit", Decimal(0)), reverse=True)
    out = []
    for r in (rows if limit is None else rows[:limit]):
        entry = {
            "userId": r["userId"],
            "profit": r.get("profit", Decimal(0)),
            "wins": int(r.get("wins", 0)),
            "losses": int(r.get("losses", 0)),
        }
        name = r.get("displayName")
        if name:
            entry["displayName"] = name
        out.append(entry)
    return out