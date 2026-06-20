"""Shared HTTP helpers for the REST Lambdas.

JSON responses with CORS headers, a serializer that renders DynamoDB Decimals as
plain numbers, and pulling the caller's id from the Cognito-verified claims. Kept
in the domain layer so every handler returns the same shape.
"""

import json
from decimal import Decimal

_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


def _default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def response(status, body):
    """An API Gateway proxy response: JSON body, CORS headers, Decimals as numbers."""
    return {
        "statusCode": status,
        "headers": _HEADERS,
        "body": json.dumps(body, default=_default),
    }


def user_id(event):
    """The authenticated caller's id, from the token claim the Cognito authorizer
    verified. Never trust a user id from the request body or query. Returns None if
    absent.
    """
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("claims", {})
        .get("sub")
    )