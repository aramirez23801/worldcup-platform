"""Push live updates to the SPA over the WebSocket API.

Each user's live connections are recorded in WsConnections (keyed by
connectionId, with gsi_user on userId). Server-side events such as settlement
call push_to_user to deliver a payload to every live connection a user has,
pruning any that have gone away. The $default route uses post_to_connection to
reply on a single connection.
"""

import json
import os

import boto3
from boto3.dynamodb.conditions import Key

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ENDPOINT = os.environ.get("WS_ENDPOINT")

_ddb = boto3.resource("dynamodb", region_name=_REGION)
_conns = _ddb.Table(os.environ.get("WS_CONNECTIONS_TABLE", "WsConnections"))
_mgmt = boto3.client("apigatewaymanagementapi", region_name=_REGION, endpoint_url=_ENDPOINT)


def post_to_connection(connection_id, payload):
    """Send one message to one connection. Returns True if delivered, False if the
    connection has gone away (the caller may prune it)."""
    try:
        _mgmt.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(payload).encode("utf-8"),
        )
        return True
    except _mgmt.exceptions.GoneException:
        return False


def push_to_user(user_id, payload):
    """Deliver a payload to every live connection a user has. Connections that have
    gone away are removed from the registry. Returns the number delivered."""
    delivered = 0
    start_key = None
    while True:
        params = {
            "IndexName": "gsi_user",
            "KeyConditionExpression": Key("userId").eq(user_id),
        }
        if start_key:
            params["ExclusiveStartKey"] = start_key
        resp = _conns.query(**params)
        for item in resp.get("Items", []):
            connection_id = item["connectionId"]
            try:
                if post_to_connection(connection_id, payload):
                    delivered += 1
                else:
                    _conns.delete_item(Key={"connectionId": connection_id})
            except Exception as exc:
                # One bad connection (throttle, oversized payload, a failed prune)
                # must not abort delivery to this user's other connections.
                print("push_to_user: skipping connection", connection_id,
                      type(exc).__name__, str(exc))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    return delivered


def broadcast(payload):
    """Deliver a payload to every live connection. A running-score update is relevant
    to everyone watching the card, not one user, so this fans out to all connections
    and prunes any that have gone away. Returns the number delivered.

    A scan suits this tournament's connection volume; a higher-fanout system would
    instead maintain a subscription topic and push only to subscribed connections.
    """
    delivered = 0
    start_key = None
    while True:
        params = {}
        if start_key:
            params["ExclusiveStartKey"] = start_key
        resp = _conns.scan(**params)
        for item in resp.get("Items", []):
            connection_id = item["connectionId"]
            try:
                if post_to_connection(connection_id, payload):
                    delivered += 1
                else:
                    _conns.delete_item(Key={"connectionId": connection_id})
            except Exception as exc:
                # One bad connection (throttle, oversized payload, a failed prune)
                # must not abort delivery to the rest.
                print("broadcast: skipping connection", connection_id,
                      type(exc).__name__, str(exc))
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break
    return delivered