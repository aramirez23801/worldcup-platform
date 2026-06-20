"""Notify users of bet settlement outcomes.

Settlement invokes this once per match (a durable context.invoke step), passing
only the bets it just settled. For each affected user we push a wallet+results
update over the WebSocket.

Every side effect here is best-effort: a failed balance read or push is logged
and swallowed, never re-raised. That keeps settlement's invoke step succeeding so
it is never retried into a duplicate push.
"""

import os

import boto3

from domain import push

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_WALLETS_TABLE = os.environ["WALLETS_TABLE"]

_ddb = boto3.resource("dynamodb", region_name=_REGION)
_wallets = _ddb.Table(_WALLETS_TABLE)


def _balance_for(user_id):
    resp = _wallets.get_item(Key={"userId": user_id})
    item = resp.get("Item")
    if item is None:
        return None
    return int(item["balance"])


def handler(event, context):
    match_id = event["matchId"]
    score = str(event["scoreHome"]) + "-" + str(event["scoreAway"])

    by_user = {}
    for s in event.get("settlements", []):
        by_user.setdefault(s["userId"], []).append(s)

    for user_id, outcomes in by_user.items():
        balance = None
        try:
            balance = _balance_for(user_id)
        except Exception as exc:
            print("notify: balance read failed", user_id, type(exc).__name__, str(exc))

        payload = {
            "type": "bets.settled",
            "matchId": match_id,
            "score": score,
            "balance": balance,
            "bets": [
                {
                    "betId": o["betId"],
                    "betType": o["betType"],
                    "selection": o["selection"],
                    "status": o["status"],
                    "payout": o["payout"],
                }
                for o in outcomes
            ],
        }
        try:
            push.push_to_user(user_id, payload)
        except Exception as exc:
            print("notify: push failed", user_id, type(exc).__name__, str(exc))

    return {"matchId": match_id, "users": len(by_user)}