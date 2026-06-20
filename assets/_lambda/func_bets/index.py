import json

from domain import betting, http
from domain.wallet import InsufficientFunds

# Domain exception -> (HTTP status, fixed message or None to use the exception text).
_ERRORS = {
    betting.MatchNotFound: (404, "match not found"),
    betting.MatchNotOpen: (409, "match is not open for betting"),
    betting.InvalidBet: (400, None),
    InsufficientFunds: (402, "insufficient balance"),
}

_REQUIRED = ("matchId", "betType", "selection", "stake")


def handler(event, context):
    user_id = http.user_id(event)
    if not user_id:
        return http.response(401, {"error": "unauthenticated"})

    method = event.get("httpMethod")

    if method == "GET":
        return http.response(200, betting.list_bets(user_id))

    if method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
        except (TypeError, ValueError):
            return http.response(400, {"error": "invalid JSON body"})
        missing = [f for f in _REQUIRED if body.get(f) is None]
        if missing:
            return http.response(400, {"error": f"missing fields: {', '.join(missing)}"})
        try:
            bet = betting.place_bet(
                user_id, body["matchId"], body["betType"], body["selection"], body["stake"]
            )
        except tuple(_ERRORS) as exc:
            status, message = _ERRORS[type(exc)]
            return http.response(status, {"error": message or str(exc)})
        return http.response(201, bet)

    return http.response(405, {"error": "method not allowed"})