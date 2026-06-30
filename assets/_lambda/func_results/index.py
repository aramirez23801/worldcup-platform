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
_TEAMS_TABLE = os.environ["TEAMS_TABLE"]
_EVENT_BUS = os.environ["EVENT_BUS"]
_FEED_SECRET = os.environ["FEED_SECRET"]
_FEED_URL = os.environ["FEED_URL"]

_EVENT_SOURCE = "worldcup.results"
_EVENT_DETAIL_TYPE = "match.settled"

# Reconciled against the feed each run: SCHEDULED/LIVE for live updates and settlement,
# and FINAL so an already-settled match whose feed score later changes is re-synced on
# the card (knockouts are resolved separately, out of TBD, before this pass).
_RECONCILED_STATUSES = ("SCHEDULED", "LIVE", "FINAL")

# A FINISHED match is settled only once the feed's terminal state has been unchanged
# for this long, so a late correction (a VAR reversal, extra time, a shootout) is not
# settled on prematurely. The score still mirrors live throughout; only the one-time
# settlement waits. Measured against the feed's own per-match lastUpdated.
_FINAL_CONFIRM_SECONDS = 120

_ddb = boto3.resource("dynamodb", region_name=_REGION)
_matches = _ddb.Table(_MATCHES_TABLE)
_teams = _ddb.Table(_TEAMS_TABLE)
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


def _canonical_by_norm():
    """normalize_name(teamId) -> teamId for every team, so a feed name can be resolved to
    our canonical spelling. The team set is the small, static tournament field, so one scan
    per run is cheap; normalize_name has no collisions across the canonical names, so each
    normalized form maps to exactly one team."""
    canonical = {}
    start_key = None
    while True:
        params = {"ProjectionExpression": "teamId"}
        if start_key:
            params["ExclusiveStartKey"] = start_key
        resp = _teams.scan(**params)
        for item in resp.get("Items", []):
            canonical[results.normalize_name(item["teamId"])] = item["teamId"]
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    return canonical


def _as_int(value):
    return int(value) if value is not None else None


def _feed_quiet(feed_match):
    """True once the feed's state for this match has been unchanged for the confirm
    window, the gate before we settle a FINISHED match. Uses the feed's own per-match
    lastUpdated; if it is missing or unparseable, treat the match as quiet so a feed
    without that field never stalls settlement."""
    ts = feed_match.get("lastUpdated")
    if not ts:
        return True
    try:
        updated = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return True
    age = (datetime.datetime.now(datetime.timezone.utc) - updated).total_seconds()
    return age >= _FINAL_CONFIRM_SECONDS


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


def _mark_final(match_id, score_home, score_away, external_id, decided_by, penalty_winner):
    """Move a match to FINAL with the final score, from SCHEDULED (never caught live)
    or LIVE. Returns True if this call made the transition, False if the match was
    already FINAL (another run won the race), which keeps settlement firing once.
    decided_by/penalty_winner are written only for a shootout (None on a normal
    match, where the attributes stay absent)."""
    set_expr = ("SET #s = :final, scoreHome = :h, scoreAway = :a, "
                "externalId = :ext, lastUpdated = :now")
    values = {
        ":final": "FINAL", ":h": score_home, ":a": score_away,
        ":ext": external_id, ":now": _now(),
    }
    if decided_by:
        set_expr += ", decidedBy = :db"
        values[":db"] = decided_by
    if penalty_winner:
        set_expr += ", penaltyWinner = :pw"
        values[":pw"] = penalty_winner
    try:
        _matches.update_item(
            Key={"matchId": match_id},
            UpdateExpression=set_expr,
            ConditionExpression="#s <> :final",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=values,
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _update_final_score(match_id, score_home, score_away, decided_by, penalty_winner):
    """Re-sync an ALREADY-FINAL match's displayed score to the feed (self-healing
    when the feed corrects a finished match). Updates the score only; never emits
    settlement, so the money side still fires exactly once."""
    set_expr = "SET scoreHome = :h, scoreAway = :a, lastUpdated = :now"
    values = {":h": score_home, ":a": score_away, ":now": _now(), ":final": "FINAL"}
    if decided_by:
        set_expr += ", decidedBy = :db"
        values[":db"] = decided_by
    if penalty_winner:
        set_expr += ", penaltyWinner = :pw"
        values[":pw"] = penalty_winner
    _matches.update_item(
        Key={"matchId": match_id},
        UpdateExpression=set_expr,
        ConditionExpression="#s = :final",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=values,
    )


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


def _emit_settled(match_id, score_home, score_away, outcome):
    _events.put_events(Entries=[{
        "Source": _EVENT_SOURCE,
        "DetailType": _EVENT_DETAIL_TYPE,
        "Detail": json.dumps({
            "matchId": match_id,
            "scoreHome": score_home,
            "scoreAway": score_away,
            "outcome": outcome,
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


def _resolve(fixture, feed_match, canonical):
    """RESOLVE path: map the feed's resolved teams to our canonical Teams spelling and
    write them onto a TBD fixture, opening it. Returns True if this run resolved it. If
    either feed name matches no canonical team, log it and leave the fixture TBD (never
    store an unmatched name); the next run retries, so a genuinely new feed spelling is
    loud in the logs but harmless to the data and to other matches."""
    home, away = results.resolved_pair(feed_match, canonical)
    match_id = fixture["matchId"]
    if home is None or away is None:
        raw_home = (feed_match.get("homeTeam") or {}).get("name")
        raw_away = (feed_match.get("awayTeam") or {}).get("name")
        print("results: knockout name unmatched, left TBD", match_id,
              "home", repr(raw_home), "->", repr(home),
              "away", repr(raw_away), "->", repr(away))
        return False
    if not _resolve_fixture(match_id, home, away, str(feed_match.get("id"))):
        return False
    print("results: resolved", match_id, home, "vs", away)
    return True


def _settle(fixture, feed_match, canonical):
    """FINAL path: record the result, drive settlement, flip every card to FINAL.
    Returns True if this run settled the match.

    Settlement is held until the feed's terminal state has been quiet for the confirm
    window, so a late correction is not settled on prematurely. A match already FINAL is
    never re-settled (the money side fires once); instead its displayed score is re-synced
    if the feed has since corrected it, logging a loud marker so the divergence is
    reconciled rather than silently frozen."""
    result = results.final_result(fixture, feed_match, canonical)
    if result is None:
        return False
    match_id = fixture["matchId"]
    score_home, score_away = result["scoreHome"], result["scoreAway"]

    if fixture.get("status") == "FINAL":
        _resync_final(fixture, result)
        return False

    if not _feed_quiet(feed_match):
        return False

    if not _mark_final(match_id, score_home, score_away, str(feed_match.get("id")),
                       result["decidedBy"], result["penaltyWinner"]):
        return False
    _emit_settled(match_id, score_home, score_away, result["outcome"])
    print("results: settled", match_id, str(score_home) + "-" + str(score_away),
          "outcome", result["outcome"])
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


def _resync_final(fixture, result):
    """A match we already settled is still in the feed. If the feed has since changed its
    final score, update our displayed score (self-healing) and log a loud marker -- bets
    were settled on the old score and need reconciling. Settlement is NOT re-run here."""
    match_id = fixture["matchId"]
    score_home, score_away = result["scoreHome"], result["scoreAway"]
    stored = (_as_int(fixture.get("scoreHome")), _as_int(fixture.get("scoreAway")))
    if stored == (score_home, score_away):
        return
    print("results: settled score changed", match_id,
          "stored", str(stored[0]) + "-" + str(stored[1]),
          "feed", str(score_home) + "-" + str(score_away),
          "outcome", result["outcome"])
    try:
        _update_final_score(match_id, score_home, score_away,
                            result["decidedBy"], result["penaltyWinner"])
        _broadcast("match.final", match_id, score_home, score_away)
    except Exception as exc:
        print("results: final resync failed", match_id, type(exc).__name__, str(exc))


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


def _resolve_knockouts(feed_matches, canonical):
    """Open knockout fixtures the feed has decided. The feed owns the bracket; we mirror its resolved teams onto our placeholder fixtures, matched by stage and kickoff."""
    tbd_fixtures = _fixtures_by_status("TBD")
    if not tbd_fixtures:
        return 0
    index = results.index_knockouts_by_slot(feed_matches)
    decided = results.any_knockout_decided(feed_matches)
    resolved = 0
    pending = 0
    for fixture in tbd_fixtures:
        feed_match = results.find_knockout(index, fixture)
        if feed_match is None or not results.is_resolved(feed_match):
            pending += 1
            continue
        try:
            if _resolve(fixture, feed_match, canonical):
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

    # Map feed team names to our canonical spelling once per run; both passes use it
    # (resolving knockout teams, and naming a shootout winner).
    canonical = _canonical_by_norm()

    # Open any knockout fixtures the feed has now decided, before reconciling play.
    resolved = _resolve_knockouts(feed_matches, canonical)

    index = results.index_by_pair(_fixtures_by_status(*_RECONCILED_STATUSES))
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
                if _settle(fixture, feed_match, canonical):
                    settled += 1
            else:
                if _live(fixture, feed_match):
                    live += 1
        except Exception as exc:
            print("results: match update failed", fixture["matchId"],
                  type(exc).__name__, str(exc))

    return {"checked": len(feed_matches), "resolved": resolved, "settled": settled, "live": live}