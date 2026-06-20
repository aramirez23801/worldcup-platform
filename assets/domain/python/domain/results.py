"""Results ingest: align live-feed matches with our fixtures and read their state.

The poller pulls the competition feed (football-data.org v4) and must line each
feed match up with one of our seeded fixtures, which carry the matchId that bets
reference. The feed has its own match IDs, so we match on the pair of teams.

Two real-world quirks are handled here:
  - Team names differ between sources. Names are normalised (lowercased, accents
    stripped) and a small alias table folds the few World Cup names that still
    differ (for example our "Czech Republic" is the feed's "Czechia").
  - Home/away orientation can differ, and our settlement scores are oriented to our
    fixture's teamHome/teamAway, so the feed's full-time score is re-oriented to
    match before it is emitted.

Matching is keyed on the team pair rather than the date: our fixtures were seeded
from local kickoff times while the feed is UTC, so the calendar day can differ.
Among fixtures that are not yet FINAL a given pair is unique, so the pair is a safe
key. Knockout fixtures still carrying bracket placeholders have no teamHome/teamAway
and are simply skipped until their teams are resolved.

Pure functions only; all I/O (feed fetch, table reads/writes, event emit) lives in
the handler.
"""

import unicodedata

# Our seeded (Teams-table) spelling -> the feed spelling, normalised on both sides,
# for the names that do not already normalise equal. Verified against the live feed.
_ALIASES = {
    "bosnia and herzegovina": "bosnia-herzegovina",
    "cape verde": "cape verde islands",
    "czech republic": "czechia",
    "dr congo": "congo dr",
}

# football-data.org v4 statuses. LIVE is a filter, not a status; v4 uses TIMED for an
# upcoming match. We only ever act on the terminal and in-play sets.
_FINAL = {"FINISHED", "AWARDED"}
_LIVE = {"IN_PLAY", "PAUSED"}


def normalize_name(name):
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = text.strip().lower()
    return _ALIASES.get(text, text)


def is_final(status):
    return status in _FINAL


def is_live(status):
    return status in _LIVE


def _pair_key(name_a, name_b):
    return frozenset((normalize_name(name_a), normalize_name(name_b)))


def index_by_pair(fixtures):
    """Map each resolved fixture's team pair to the fixture. Build this from
    not-yet-FINAL fixtures, where a pair is unique. Fixtures still carrying bracket
    placeholders (no teamHome/teamAway) are skipped."""
    index = {}
    for f in fixtures:
        home, away = f.get("teamHome"), f.get("teamAway")
        if not home or not away:
            continue
        index[_pair_key(home, away)] = f
    return index


def find_fixture(index, feed_match):
    """Our fixture for a feed match, or None when the feed teams are placeholders or
    no pending fixture matches this pair."""
    home = (feed_match.get("homeTeam") or {}).get("name")
    away = (feed_match.get("awayTeam") or {}).get("name")
    if not home or not away:
        return None
    return index.get(_pair_key(home, away))


def oriented_score(fixture, feed_match):
    """The full-time score as (home, away) in our fixture's orientation (teamHome
    first), or None if the feed has not populated the full-time score yet."""
    ft = feed_match.get("score", {}).get("fullTime", {})
    home, away = ft.get("home"), ft.get("away")
    if home is None or away is None:
        return None
    if normalize_name(fixture["teamHome"]) == normalize_name(feed_match["homeTeam"]["name"]):
        return int(home), int(away)
    return int(away), int(home)


# --- knockout bracket resolution ---------------------------------------------
# Knockout fixtures carry bracket placeholders (sourceHome/sourceAway like "1A",
# "W74") and no teams until the bracket resolves, so they cannot be matched on the
# team pair. The feed resolves the whole bracket itself (group ranking, best-thirds
# allocation, penalty winners), filling in each knockout match's teams once decided.
# We mirror that: find our fixture's feed match by stage and kickoff, and copy the
# teams across. Verified against the live feed: every one of our 32 knockout fixtures
# lines up with exactly one feed match by (stage, kickoff to the minute), no
# collisions. Our seeded kickoffs and the feed's utcDate agree to the minute here
# (unlike the group stage, where only the team pair is reliable).

_KNOCKOUT_STAGE = {
    "R32": "LAST_32",
    "R16": "LAST_16",
    "QF": "QUARTER_FINALS",
    "SF": "SEMI_FINALS",
    "3P": "THIRD_PLACE",
    "F": "FINAL",
}
_FEED_KNOCKOUT_STAGES = set(_KNOCKOUT_STAGE.values())


def is_knockout(stage):
    return stage in _KNOCKOUT_STAGE


def is_resolved(feed_match):
    """True once the feed has filled in both teams for a knockout match (its bracket
    slot has been decided)."""
    home = (feed_match.get("homeTeam") or {}).get("name")
    away = (feed_match.get("awayTeam") or {}).get("name")
    return bool(home) and bool(away)

def any_knockout_decided(feed_matches):
    """True once the feed has resolved at least one knockout match, i.e. the bracket has begun filling in. Used to keep "knockouts still pending" logging quiet until the tournament actually reaches the knockout stage."""
    return any(
        m.get("stage") in _FEED_KNOCKOUT_STAGES and is_resolved(m)
        for m in feed_matches
    )

def _slot_key(feed_stage, kickoff_iso):
    return (feed_stage, kickoff_iso[:16])


def index_knockouts_by_slot(feed_matches):
    """Map each knockout feed match's (stage, kickoff-minute) slot to the feed match,
    for looking up the feed match behind one of our placeholder fixtures."""
    index = {}
    for m in feed_matches:
        stage = m.get("stage")
        if stage not in _FEED_KNOCKOUT_STAGES:
            continue
        date = m.get("utcDate")
        if not date:
            continue
        index[_slot_key(stage, date)] = m
    return index


def find_knockout(index, fixture):
    """The feed match for one of our knockout fixtures, looked up by its stage and
    kickoff slot, or None if the feed has no match in that slot."""
    feed_stage = _KNOCKOUT_STAGE.get(fixture.get("stage"))
    kickoff = fixture.get("kickoff")
    if feed_stage is None or not kickoff:
        return None
    return index.get(_slot_key(feed_stage, kickoff))


def resolved_pair(feed_match):
    """The (home, away) team names the feed has resolved for a knockout match."""
    return feed_match["homeTeam"]["name"], feed_match["awayTeam"]["name"]