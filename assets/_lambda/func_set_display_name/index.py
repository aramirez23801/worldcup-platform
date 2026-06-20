"""Set the caller's leaderboard display name.

Writes only the authenticated user's own displayName onto their leaderboard row.
The user id comes from the Cognito-verified token claim (never the request body),
so a caller can only ever rename themselves. The body carries just the name; the
domain layer validates and caps it.
"""

import json

from domain import http, leaderboard


def handler(event, context):
    user_id = http.user_id(event)
    if not user_id:
        return http.response(401, {"error": "unauthenticated"})

    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        return http.response(400, {"error": "invalid JSON body"})

    name = leaderboard.set_display_name(user_id, body.get("name"))
    if name is None:
        return http.response(400, {"error": "name is required"})

    return http.response(200, {"displayName": name})