from domain import http, matches, standings


def handler(event, context):
    resource = event.get("resource")

    if resource == "/matches":
        params = event.get("queryStringParameters") or {}
        return http.response(200, matches.list_matches(params.get("status")))

    if resource == "/matches/{id}":
        match = matches.get_match((event.get("pathParameters") or {}).get("id"))
        if match is None:
            return http.response(404, {"error": "match not found"})
        return http.response(200, match)

    if resource == "/standings":
        return http.response(200, standings.group_tables())

    if resource == "/bracket":
        return http.response(200, matches.bracket())

    return http.response(404, {"error": "not found"})