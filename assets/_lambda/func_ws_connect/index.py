# WebSocket $connect route.
#
# Runs after the authorizer has allowed the handshake. It records the live
# connection so the backend can later push updates to this user: the row keys a
# connectionId to the userId (the authorizer's "sub"), and gsi_user on the table
# lets settlement look up every live connection for a given user.
import os
from datetime import datetime, timezone
 
import boto3
 
_TABLE = os.environ['WS_CONNECTIONS_TABLE']
# TTL backstop in case $disconnect is never delivered (abnormal client drop).
# A connection lives at most 2h (API Gateway max duration), so 3h covers it.
_TTL_SECONDS = 3 * 60 * 60
 
_ddb = boto3.client('dynamodb')
 
 
def handler(event, context):
    ctx = event['requestContext']
    connection_id = ctx['connectionId']
    user_id = ctx['authorizer']['sub']
 
    now = datetime.now(timezone.utc)
    _ddb.put_item(
        TableName=_TABLE,
        Item={
            'connectionId': {'S': connection_id},
            'userId': {'S': user_id},
            'connectedAt': {'S': now.isoformat()},
            'expiresAt': {'N': str(int(now.timestamp()) + _TTL_SECONDS)},
        },
    )
    return {'statusCode': 200}