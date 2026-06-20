"""Results ingest poller.

Runs on a schedule. Pulls the World Cup match feed (football-data.org v4) and mirrors
it onto our fixtures. Three responsibilities, all "reflect what the feed says":

  - Resolve: knockout fixtures are seeded with bracket placeholders and no teams. The
    feed owns the bracket (group ranking, best-thirds allocation, penalty winners) and
    fills in each knockout match's teams once decided. We copy those teams onto our
    placeholder fixture, matched by stage and kickoff, and open it for betting
    (TBD -> SCHEDULED). From then on it has real teams and flows through the live and
    final paths like any other match.

  - Live: while a match is in play, mirror its running score onto the Matches table
    and broadcast it to every connected client. We write and broadcast only when the
    score moves (or the match first goes live), never on every poll.

  - Final: once the feed reports a match FINISHED, write the final status and score and
    emit match.settled onto the events bus, driving the settlement -> notify pipeline.
    A final-score broadcast flips every viewer's card to FINAL.

A match's life is TBD (knockouts only) -> SCHEDULED -> LIVE -> FINAL. Each transition
is a conditional write, so it happens once even though the poller revisits the whole
feed every run, and a race between overlapping runs is caught by the condition.

The feed fetch and each per-match write are guarded: a feed outage or a single bad
match is logged and skipped so the poller keeps running and retries next tick.
"""

import datetime
import json
import os
import urllib.request

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from domain import elo
from domain import push
from domain import results

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MATCHES_TABLE = os.environ["MATCHES_TABLE"]
_EVENT_BUS = os.environ["EVENT_BUS"]
_FEED_SECRET = os.environ["FEED_SECRET"]
_FEED_URL = os.environ["FEED_URL"]

_EVENT_SOURCE = "worldcup.results"
_EVENT_DETAIL_TYPE = "match.settled"

# Reconciled against the feed for live updates and settlement (knockouts are resolved
# separately, out of TBD, before this pass).
_PENDING_STATUSES = ("SCHEDULED", "LIVE")

_ddb = boto3.resource("dynamodb", region_name=_REGION)
_matches = _ddb.Table(_MATCHES_TABLE)
_events = boto3.client("events", region_name=_REGION)
_secrets = boto3.client("secretsmanager", region_name=_REGION)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _feed_token():
    # Read per run so a rotated or just-populated token is picked up next tick.
    return _secrets.get_secret_value(SecretId=_FEED_SECRET)["SecretString"]


def _fetch_feed(token):
    request = urllib.request.Request(_FEED_URL, headers={"X-Auth-Token": token})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _fixtures_by_status(*statuses):
    """Fixtures in the given statuses, read through the status index."""
    fixtures = []
    for status in statuses:
        start_key = None
        while True:
            params = {
                "IndexName": "gsi_status_kickoff",
                "KeyConditionExpression": Key("status").eq(status),
            }
            if start_key:
                params["ExclusiveStartKey"] = start_key
            resp = _matches.query(**params)
            fixtures.extend(resp.get("Items", []))
            start_key = resp.get("LastEvaluatedKey")
            if not start_key:
                break
    return fixtures


def _as_int(value):
    return int(value) if value is not None else None


def _resolve_fixture(match_id, team_home, team_away, external_id):
    """Move a knockout fixture TBD -> SCHEDULED with the feed's resolved teams. Returns
    True if this call made the transition, False if it was already resolved."""
    try:
        _matches.update_item(
            Key={"matchId": match_id},
            UpdateExpression=(
                "SET #s = :sched, teamHome = :h, teamAway = :a, "
                "externalId = :ext, lastUpdated = :now"
            ),
            ConditionExpression="#s = :tbd",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":sched": "SCHEDULED",
                ":tbd": "TBD",
                ":h": team_home,
                ":a": team_away,
                ":ext": external_id,
                ":now": _now(),
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _mark_final(match_id, score_home, score_away, external_id):
    """Move a match to FINAL with the final score, from SCHEDULED (never caught live)
    or LIVE. Returns True if this call made the transition, False if the match was
    already FINAL (another run won the race), which keeps settlement firing once."""
    try:
        _matches.update_item(
            Key={"matchId": match_id},
            UpdateExpression=(
                "SET #s = :final, scoreHome = :h, scoreAway = :a, "
                "externalId = :ext, lastUpdated = :now"
            ),
            ConditionExpression="#s <> :final",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":final": "FINAL",
                ":h": score_home,
                ":a": score_away,
                ":ext": external_id,
                ":now": _now(),
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _mark_live(match_id, score_home, score_away):
    """Set a match LIVE with its running score. Returns True if written, False if the
    match has already gone FINAL (a concurrent run settled it; do not pull it back)."""
    try:
        _matches.update_item(
            Key={"matchId": match_id},
            UpdateExpression="SET #s = :live, scoreHome = :h, scoreAway = :a, lastUpdated = :now",
            ConditionExpression="#s <> :final",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":live": "LIVE",
                ":final": "FINAL",
                ":h": score_home,
                ":a": score_away,
                ":now": _now(),
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _live_changed(fixture, score_home, score_away):
    """True when a live match should be (re)broadcast: it is newly live (still
    SCHEDULED on our side) or its score has moved since we last stored it. Keeps an
    unchanged score from being rebroadcast on every poll."""
    if fixture.get("status") != "LIVE":
        return True
    stored = (_as_int(fixture.get("scoreHome")), _as_int(fixture.get("scoreAway")))
    return stored != (score_home, score_away)


def _emit_settled(match_id, score_home, score_away):
    _events.put_events(Entries=[{
        "Source": _EVENT_SOURCE,
        "DetailType": _EVENT_DETAIL_TYPE,
        "Detail": json.dumps({
            "matchId": match_id,
            "scoreHome": score_home,
            "scoreAway": score_away,
        }),
        "EventBusName": _EVENT_BUS,
    }])


def _broadcast(kind, match_id, score_home, score_away):
    push.broadcast({
        "type": kind,
        "matchId": match_id,
        "scoreHome": score_home,
        "scoreAway": score_away,
    })


def _resolve(fixture, feed_match):
    """RESOLVE path: write the feed's resolved teams onto a TBD fixture and open it.
    Returns True if this run resolved it."""
    home, away = results.resolved_pair(feed_match)
    match_id = fixture["matchId"]
    if not _resolve_fixture(match_id, home, away, str(feed_match.get("id"))):
        return False
    print("results: resolved", match_id, home, "vs", away)
    return True


def _settle(fixture, feed_match):
    """FINAL path: record the result, drive settlement, flip every card to FINAL.
    Returns True if this run settled the match."""
    score = results.oriented_score(fixture, feed_match)
    if score is None:
        return False
    match_id = fixture["matchId"]
    score_home, score_away = score
    if not _mark_final(match_id, score_home, score_away, str(feed_match.get("id"))):
        return False
    _emit_settled(match_id, score_home, score_away)
    print("results: settled", match_id, str(score_home) + "-" + str(score_away))
    try:
        _broadcast("match.final", match_id, score_home, score_away)
    except Exception as exc:
        print("results: final broadcast failed", match_id, type(exc).__name__, str(exc))
    # Knockout results feed the forecaster: update both teams' Elo. _settle runs once
    # per match (the FINAL-write gate above), so this applies exactly once. Best-effort:
    # a forecaster-rating blip must not disturb settlement.
    if results.is_knockout(fixture.get("stage")):
        try:
            elo.apply_match_elo(fixture["teamHome"], fixture["teamAway"], score_home, score_away)
            print("results: elo updated", match_id, fixture["teamHome"], "vs", fixture["teamAway"])
        except Exception as exc:
            print("results: elo update failed", match_id, type(exc).__name__, str(exc))
    return True


def _live(fixture, feed_match):
    """LIVE path: mirror the running score and broadcast it, but only when it has
    moved. Returns True if this run broadcast a live update."""
    score = results.oriented_score(fixture, feed_match)
    if score is None:
        return False
    score_home, score_away = score
    if not _live_changed(fixture, score_home, score_away):
        return False
    match_id = fixture["matchId"]
    if not _mark_live(match_id, score_home, score_away):
        return False
    try:
        _broadcast("match.live", match_id, score_home, score_away)
    except Exception as exc:
        print("results: live broadcast failed", match_id, type(exc).__name__, str(exc))
    print("results: live", match_id, str(score_home) + "-" + str(score_away))
    return True


def _resolve_knockouts(feed_matches):
    """Open knockout fixtures the feed has decided. The feed owns the bracket; we mirror its resolved teams onto our placeholder fixtures, matched by stage and kickoff."""
    index = results.index_knockouts_by_slot(feed_matches)
    decided = results.any_knockout_decided(feed_matches)
    resolved = 0
    pending = 0
    for fixture in _fixtures_by_status("TBD"):
        feed_match = results.find_knockout(index, fixture)
        if feed_match is None or not results.is_resolved(feed_match):
            pending += 1
            continue
        try:
            if _resolve(fixture, feed_match):
                resolved += 1
        except Exception as exc:
            print("results: resolve failed", fixture["matchId"], type(exc).__name__, str(exc))
    # Once the bracket starts resolving, surface how many knockout fixtures are still
    # closed to betting, so one stuck unresolved (for example a kickoff-time mismatch)
    # is visible rather than silent. Stays quiet through the group stage.
    if decided and pending:
        print("results: knockouts pending", pending)
    return resolved

def handler(event, context):
    try:
        feed = _fetch_feed(_feed_token())
    except Exception as exc:
        print("results: feed fetch failed", type(exc).__name__, str(exc))
        return {"checked": 0, "resolved": 0, "settled": 0, "live": 0, "error": "feed_unavailable"}

    feed_matches = feed.get("matches", [])

    # Open any knockout fixtures the feed has now decided, before reconciling play.
    resolved = _resolve_knockouts(feed_matches)

    index = results.index_by_pair(_fixtures_by_status(*_PENDING_STATUSES))
    settled = 0
    live = 0
    for feed_match in feed_matches:
        status = feed_match.get("status")
        is_final = results.is_final(status)
        is_live = results.is_live(status)
        if not (is_final or is_live):
            continue
        fixture = results.find_fixture(index, feed_match)
        if fixture is None:
            continue
        try:
            if is_final:
                if _settle(fixture, feed_match):
                    settled += 1
            else:
                if _live(fixture, feed_match):
                    live += 1
        except Exception as exc:
            print("results: match update failed", fixture["matchId"],
                  type(exc).__name__, str(exc))

    return {"checked": len(feed_matches), "resolved": resolved, "settled": settled, "live": live}