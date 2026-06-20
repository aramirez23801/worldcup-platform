# WebSocket $default route.
#
# Catches any client message that is not $connect or $disconnect. The SPA is
# mostly a receiver (the backend pushes updates), so the one message it sends is
# a keepalive ping, answered here with a pong on the same connection. An idle
# WebSocket is closed at 10 minutes, so the ping keeps the connection alive and
# the pong confirms the backend is responsive. Anything else is accepted and
# ignored.
import json

from domain import push


def handler(event, context):
    connection_id = event['requestContext']['connectionId']

    body = event.get('body')
    try:
        message = json.loads(body) if body else {}
    except (TypeError, ValueError):
        message = {}

    if message.get('action') == 'ping':
        push.post_to_connection(connection_id, {'action': 'pong'})

    return {'statusCode': 200}