"""Match reads shared by the REST API and the agent: list, get, and bracket."""

import os

import boto3
from boto3.dynamodb.conditions import Key

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_matches = _ddb.Table(os.environ.get("MATCHES_TABLE", "Matches"))

_KNOCKOUT_STAGES = ("R32", "R16", "QF", "SF", "3P", "F")


def get_match(match_id):
    """A single match by id, or None if there is no such match."""
    return _matches.get_item(Key={"matchId": match_id}).get("Item")


def list_matches(status=None):
    """All matches, or those in one status, ordered by kickoff.

    With a status, this queries the status/kickoff index (already ordered). With
    no status it scans the bounded table and sorts in memory.
    """
    if status:
        resp = _matches.query(
            IndexName="gsi_status_kickoff",
            KeyConditionExpression=Key("status").eq(status),
        )
        return resp.get("Items", [])
    return sorted(_scan_all(), key=lambda m: m.get("kickoff", ""))


def bracket():
    """The knockout matches only, ordered by kickoff, for the bracket view."""
    knockout = [m for m in _scan_all() if m.get("stage") in _KNOCKOUT_STAGES]
    return sorted(knockout, key=lambda m: m.get("kickoff", ""))


def _normalize(name):
    return name.strip().casefold()


def find_open_match(team_a, team_b):
    """The SCHEDULED fixture for this unordered team pair, or None. Resolves a bet on two
    named teams to the fixture it binds to. Names are matched case-insensitively and must
    be the exact national-team names the rest of the system uses. A given pair is
    scheduled at most once at a time, so the match is unambiguous."""
    want = frozenset((_normalize(team_a), _normalize(team_b)))
    for match in list_matches("SCHEDULED"):
        pair = frozenset((_normalize(match.get("teamHome", "")), _normalize(match.get("teamAway", ""))))
        if pair == want:
            return match
    return None


def _scan_all():
    items, kwargs = [], {}
    while True:
        resp = _matches.scan(**kwargs)
        items.extend(resp.get("Items", []))
        key = resp.get("LastEvaluatedKey")
        if not key:
            return items
        kwargs["ExclusiveStartKey"] = key