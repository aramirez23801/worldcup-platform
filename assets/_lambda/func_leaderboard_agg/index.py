"""Leaderboard aggregator.

Driven by the Bets table stream. When a bet settles (status goes from PENDING to WON or
LOST), add its realized profit to the player's tournament standing and broadcast the
refreshed leaderboard to every connected client. A match settling flips many bets at
once, so the stream delivers them in one batch: we apply every settlement in the batch
and broadcast the board once.

DynamoDB Streams is at-least-once, so a rare retry could count a settlement twice; for
this fake-coin game that is an accepted limit (same posture as the deferred bet
idempotency).

A failing record is logged and skipped so one bad record cannot wedge the shard. The
tradeoff is the opposite error: because the batch still returns success, the stream will
not redeliver, so a record that raised in record_settlement is a PERMANENT under-count
on the board (that player's profit/win/loss for that bet is never applied) and the board
silently diverges from the already-credited wallet. The drop is emitted with a stable
"leaderboard: dropped settlement" marker (see handler) so Phase 9 can alarm on it; making
the write idempotent and adding a rebuild-from-Bets reconcile is deferred to the Phase 9
leaderboard pass.
"""

import logging

from boto3.dynamodb.types import TypeDeserializer

from domain import leaderboard
from domain import push

logger = logging.getLogger("worldcup.leaderboard")
logger.setLevel(logging.INFO)

_deser = TypeDeserializer()
_SETTLED = ("WON", "LOST")


def _plain(image):
    """A DynamoDB stream image (type-tagged) as a plain dict."""
    return {key: _deser.deserialize(value) for key, value in image.items()}


def _settlement(record):
    """The new image as a plain dict if this record is a bet settling
    (PENDING -> WON/LOST), else None. Placements (INSERT), deletions (REMOVE), and any
    other change are ignored."""
    if record.get("eventName") != "MODIFY":
        return None
    data = record.get("dynamodb", {})
    old, new = data.get("OldImage"), data.get("NewImage")
    if not old or not new:
        return None
    old_status = _deser.deserialize(old["status"]) if "status" in old else None
    new_status = _deser.deserialize(new["status"]) if "status" in new else None
    if old_status == "PENDING" and new_status in _SETTLED:
        return _plain(new)
    return None


def handler(event, context):
    recorded = 0
    for record in event.get("Records", []):
        try:
            bet = _settlement(record)
            if bet is None:
                continue
            leaderboard.record_settlement(
                bet["userId"], bet["status"], bet["stake"], bet["oddsSnapshot"]
            )
            recorded += 1
        except Exception:
            logger.exception("leaderboard: dropped settlement")
    if recorded:
        board = []
        for row in leaderboard.standings():
            entry = {
                "userId": row["userId"],
                "profit": int(row["profit"]),
                "wins": row["wins"],
                "losses": row["losses"],
            }
            if "displayName" in row:
                entry["displayName"] = row["displayName"]
            board.append(entry)
        try:
            push.broadcast({"type": "leaderboard", "standings": board})
        except Exception:
            logger.exception("leaderboard: broadcast failed")

    return {"recorded": recorded}