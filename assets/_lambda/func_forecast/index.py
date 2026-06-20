from domain import forecast, http, matches


def handler(event, context):
    match_id = (event.get("pathParameters") or {}).get("id")
    match = matches.get_match(match_id)
    if match is None:
        return http.response(404, {"error": "match not found"})

    home, away = match.get("teamHome"), match.get("teamAway")
    if not home or not away:
        # Knockout match whose teams are not resolved yet.
        return http.response(409, {"error": "teams for this match are not set yet"})

    result = forecast.forecast_match(home, away)
    if "error" in result:
        return http.response(422, result)
    return http.response(200, {"matchId": match_id, **result})