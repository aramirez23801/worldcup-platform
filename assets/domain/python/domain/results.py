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


def canonical_name(feed_name, canonical_by_norm):
    """Map a feed team name to our canonical Teams-table spelling, or None if no team
    matches. canonical_by_norm maps normalize_name(teamId) -> teamId and is built by the
    caller from the Teams table; routing the feed name through the same normalize_name used
    for fixture matching means the _ALIASES pairs (Czechia, Congo DR, ...) and plain
    accent/punctuation differences (Curaçao, the hyphen in Bosnia-Herzegovina) are folded
    by one path, with no second map to keep in sync."""
    if not feed_name:
        return None
    return canonical_by_norm.get(normalize_name(feed_name))


def resolved_pair(feed_match, canonical_by_norm):
    """The (home, away) team names for a resolved knockout match, mapped from the feed's
    names to our canonical Teams-table spelling. Either element is None when the feed name
    matches no canonical team; the caller must not store a None (an unmatched name)."""
    home = canonical_name((feed_match.get("homeTeam") or {}).get("name"), canonical_by_norm)
    away = canonical_name((feed_match.get("awayTeam") or {}).get("name"), canonical_by_norm)
    return home, away


# --- final result, penalty-aware ---------------------------------------------
# A knockout can be level after normal (and extra) time and decided on penalties.
# The feed records that as duration PENALTY_SHOOTOUT, a level regularTime/extraTime,
# and a fullTime that folds in the shootout (e.g. regulation 1-1, fullTime 5-6).
# Settlement must pay the 1X2 market to the team that ADVANCED (the shootout winner)
# while the goals market and the displayed score count only match goals. So we split
# the finished match into a display/goals score (normal time) and an outcome (who
# went through), and carry both through match.settled.

_PENALTY_SHOOTOUT = "PENALTY_SHOOTOUT"


def _score_pair(feed_match, key):
    """A (home, away) int pair from one of the feed's score sub-objects (fullTime,
    regularTime, ...), or (None, None) when the feed has not populated it."""
    sub = (feed_match.get("score") or {}).get(key) or {}
    home, away = sub.get("home"), sub.get("away")
    if home is None or away is None:
        return None, None
    return int(home), int(away)


def _our_home_is_feed_home(fixture, feed_match):
    return normalize_name(fixture["teamHome"]) == normalize_name(feed_match["homeTeam"]["name"])


_FEED_WINNER = {"HOME_TEAM": "HOME", "AWAY_TEAM": "AWAY", "DRAW": "DRAW"}


def _feed_winner(feed_match, ft_home, ft_away):
    """Who won, in the feed's own orientation (HOME/AWAY/DRAW). Prefer the feed's explicit
    score.winner field; for a shootout fall back to the penalty tally; else compare full
    time. Full time is the last resort because on a shootout it folds in the penalties and
    the feed can publish a (wrong) full time before the winner is actually settled -- the
    failure that mis-paid two penalty matches."""
    score = feed_match.get("score") or {}
    explicit = _FEED_WINNER.get(score.get("winner"))
    if explicit:
        return explicit
    if score.get("duration") == _PENALTY_SHOOTOUT:
        pen_home, pen_away = _score_pair(feed_match, "penalties")
        if pen_home is not None and pen_home != pen_away:
            return "HOME" if pen_home > pen_away else "AWAY"
    return "HOME" if ft_home > ft_away else "AWAY" if ft_away > ft_home else "DRAW"


def final_result(fixture, feed_match, canonical_by_norm):
    """The settled view of a finished feed match, oriented to our fixture:
        {scoreHome, scoreAway, outcome, decidedBy, penaltyWinner}
    or None if the feed has not populated the full-time score yet.

    scoreHome/scoreAway is the score in normal (and extra) time, excluding any
    shootout, so a penalty-decided knockout stores its level score (e.g. 1-1) and
    the Over/Under market counts only match goals. outcome is who advanced --
    HOME/DRAW/AWAY in our fixture's orientation -- which for a shootout is the
    penalty winner, not the level score. Settlement uses outcome for 1X2 and the
    stored score for OU. decidedBy/penaltyWinner annotate a shootout for the UI."""
    ft_home, ft_away = _score_pair(feed_match, "fullTime")
    if ft_home is None:
        return None
    duration = (feed_match.get("score") or {}).get("duration")

    if duration == _PENALTY_SHOOTOUT:
        reg_home, reg_away = _score_pair(feed_match, "regularTime")
        if reg_home is None:  # feed gave only fullTime; fall back to it for display
            reg_home, reg_away = ft_home, ft_away
        et_home, et_away = _score_pair(feed_match, "extraTime")
        disp_home = reg_home + (et_home or 0)
        disp_away = reg_away + (et_away or 0)
    else:
        disp_home, disp_away = ft_home, ft_away

    # Who advanced, from the feed's authoritative winner (not the score, which is level
    # on a shootout), in our fixture's orientation.
    feed_outcome = _feed_winner(feed_match, ft_home, ft_away)
    home_is_feed_home = _our_home_is_feed_home(fixture, feed_match)
    score_home, score_away = (disp_home, disp_away) if home_is_feed_home else (disp_away, disp_home)
    if feed_outcome == "DRAW":
        outcome = "DRAW"
    else:
        outcome = "HOME" if (feed_outcome == "HOME") == home_is_feed_home else "AWAY"

    decided_by = "PENALTIES" if duration == _PENALTY_SHOOTOUT else None
    penalty_winner = None
    if decided_by == "PENALTIES" and feed_outcome in ("HOME", "AWAY"):
        winner_side = "homeTeam" if feed_outcome == "HOME" else "awayTeam"
        penalty_winner = canonical_name(
            (feed_match.get(winner_side) or {}).get("name"), canonical_by_norm)

    return {
        "scoreHome": score_home, "scoreAway": score_away,
        "outcome": outcome, "decidedBy": decided_by, "penaltyWinner": penalty_winner,
    }