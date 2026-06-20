from domain import http, leaderboard


def handler(event, context):
    user_id = http.user_id(event)
    if not user_id:
        return http.response(401, {"error": "unauthenticated"})

    board = []
    for row in leaderboard.standings():
        entry = {
            "userId": row["userId"],
            "profit": int(row["profit"]),
            "wins": row["wins"],
            "losses": row["losses"],
        }
        if "displayName" in row:
            entry["displayName"] = row["displayName"]
        board.append(entry)
    return http.response(200, {"leaderboard": board})