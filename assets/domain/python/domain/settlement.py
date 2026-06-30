"""Settle a finished match: turn each PENDING bet on it into WON or LOST and pay
winners.

Pure outcome logic (outcome_1x2, bet_won) plus the transactional per-bet settle
(settle_bet) that the settlement workflow runs as a durable step. Every write is
conditional on the bet still being PENDING, so a durable replay or a redelivered
message settles each bet exactly once.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import boto3
from boto3.dynamodb.conditions import Key

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_client = boto3.client("dynamodb", region_name=_REGION)
_bets = _ddb.Table(os.environ.get("BETS_TABLE", "Bets"))
_BETS_TABLE = _bets.table_name
_WALLETS_TABLE = os.environ.get("WALLETS_TABLE", "Wallets")


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def outcome_1x2(match):
    """The 1X2 result of a played match from its score: HOME, DRAW, or AWAY."""
    home = int(match["scoreHome"])
    away = int(match["scoreAway"])
    if home > away:
        return "HOME"
    if away > home:
        return "AWAY"
    return "DRAW"


def bet_won(bet, match):
    """Whether a bet won, given the final result. 1X2 settles on the match outcome
    (who advanced): the poller supplies it as match["outcome"], which for a knockout
    decided on penalties is the shootout winner rather than the level score; older
    events without it fall back to the score. OU settles on match goals (the stored
    normal-time score), so a shootout never tips a goals market."""
    selection = bet["selection"]
    if bet["betType"] == "1X2":
        return selection == (match.get("outcome") or outcome_1x2(match))
    if bet["betType"] == "OU25":
        total = int(match["scoreHome"]) + int(match["scoreAway"])
        return (selection == "OVER" and total >= 3) or (selection == "UNDER" and total <= 2)
    return False


def pending_bets_for_match(match_id):
    """Every still-PENDING bet on a match, via gsi_match_status (paginated)."""
    bets = []
    start_key = None
    while True:
        params = {
            "IndexName": "gsi_match_status",
            "KeyConditionExpression": Key("matchId").eq(match_id) & Key("status").eq("PENDING"),
        }
        if start_key:
            params["ExclusiveStartKey"] = start_key
        resp = _bets.query(**params)
        bets.extend(resp.get("Items", []))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    return bets


def settle_bet(bet, match):
    """Settle one bet against the final score, crediting the wallet on a win. The
    write is conditional on the bet still being PENDING, so settling the same bet
    twice (a durable replay or a redelivered message) is a no-op the second time.
    Returns the result; settled is False if the bet had already been settled.
    """
    user_id = bet["userId"]
    bet_id = bet["betId"]
    now = _now_iso()
    bets_key = {"userId": {"S": user_id}, "betId": {"S": bet_id}}

    if bet_won(bet, match):
        payout = (bet["stake"] * bet["oddsSnapshot"]).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        try:
            _client.transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": _BETS_TABLE,
                    "Key": bets_key,
                    "UpdateExpression": "SET #s = :won, payout = :p, settledAt = :t",
                    "ConditionExpression": "#s = :pending",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {
                        ":won": {"S": "WON"}, ":p": {"N": str(payout)},
                        ":t": {"S": now}, ":pending": {"S": "PENDING"},
                    },
                }},
                {"Update": {
                    "TableName": _WALLETS_TABLE,
                    "Key": {"userId": {"S": user_id}},
                    "UpdateExpression": "SET balance = balance + :p, updatedAt = :t",
                    "ConditionExpression": "attribute_exists(userId)",
                    "ExpressionAttributeValues": {":p": {"N": str(payout)}, ":t": {"S": now}},
                }},
            ])
        except _client.exceptions.TransactionCanceledException as exc:
            reasons = exc.response.get("CancellationReasons", [])
            # reasons[0] is the Bets update; a PENDING-condition miss means the bet
            # was already settled, the idempotent no-op. Anything else is real.
            if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
                return {"betId": bet_id, "status": "WON", "payout": payout, "settled": False}
            raise
        return {"betId": bet_id, "status": "WON", "payout": payout, "settled": True}

    # Lost: flip the bet only, no wallet change.
    try:
        _client.update_item(
            TableName=_BETS_TABLE,
            Key=bets_key,
            UpdateExpression="SET #s = :lost, payout = :z, settledAt = :t",
            ConditionExpression="#s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":lost": {"S": "LOST"}, ":z": {"N": "0"},
                ":t": {"S": now}, ":pending": {"S": "PENDING"},
            },
        )
    except _client.exceptions.ConditionalCheckFailedException:
        return {"betId": bet_id, "status": "LOST", "payout": Decimal("0"), "settled": False}
    return {"betId": bet_id, "status": "LOST", "payout": Decimal("0"), "settled": True}