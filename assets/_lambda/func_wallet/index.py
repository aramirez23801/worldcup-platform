from domain import http, wallet


def handler(event, context):
    user_id = http.user_id(event)
    if not user_id:
        return http.response(401, {"error": "unauthenticated"})

    balance = wallet.get_balance(user_id)
    if balance is None:
        return http.response(404, {"error": "wallet not found"})
    return http.response(200, {"userId": user_id, "balance": balance})