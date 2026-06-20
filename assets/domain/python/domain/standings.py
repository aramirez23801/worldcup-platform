"""Group standings computed from finished group matches."""
 
import os
 
import boto3
from boto3.dynamodb.conditions import Attr
 
_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_matches = _ddb.Table(os.environ.get("MATCHES_TABLE", "Matches"))
 
 
def group_tables():
    """Standings for every group as {group: [rows, best first]}.
 
    Rows are built from FINAL group matches. Teams from not-yet-played matches
    appear with a zeroed row, so each group shows its four teams from the start.
    Order is points, then goal difference, then goals for, then team name. Full
    FIFA tiebreakers (head-to-head, fair play) are out of scope; this ordering is
    for display, and official qualification comes from the results feed.
    """
    by_group = {}
    for m in _scan_group_matches():
        by_group.setdefault(m["group"], []).append(m)
 
    tables = {}
    for group, matches in by_group.items():
        rows = {}
        for m in matches:
            for team in (m.get("teamHome"), m.get("teamAway")):
                if team and team not in rows:
                    rows[team] = _blank_row(team)
        for m in matches:
            if m.get("status") != "FINAL":
                continue
            home, away = m["teamHome"], m["teamAway"]
            hs, as_ = int(m["scoreHome"]), int(m["scoreAway"])
            _apply(rows[home], hs, as_)
            _apply(rows[away], as_, hs)
        tables[group] = sorted(
            rows.values(),
            key=lambda r: (-r["points"], -r["goalDifference"], -r["goalsFor"], r["team"]),
        )
    return tables
 
 
def _blank_row(team):
    return {"team": team, "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "goalsFor": 0, "goalsAgainst": 0, "goalDifference": 0, "points": 0}
 
 
def _apply(row, gf, ga):
    row["played"] += 1
    row["goalsFor"] += gf
    row["goalsAgainst"] += ga
    row["goalDifference"] = row["goalsFor"] - row["goalsAgainst"]
    if gf > ga:
        row["won"] += 1
        row["points"] += 3
    elif gf == ga:
        row["drawn"] += 1
        row["points"] += 1
    else:
        row["lost"] += 1
 
 
def _scan_group_matches():
    items, kwargs = [], {"FilterExpression": Attr("stage").eq("GROUP")}
    while True:
        resp = _matches.scan(**kwargs)
        items.extend(resp.get("Items", []))
        key = resp.get("LastEvaluatedKey")
        if not key:
            return items
        kwargs["ExclusiveStartKey"] = key