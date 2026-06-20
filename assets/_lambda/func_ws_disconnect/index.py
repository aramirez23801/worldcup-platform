# WebSocket $disconnect route.
#
# Fires when a connection closes (client close, idle timeout, or the 2h max).
# Removes the registry row so the backend stops trying to push to a dead
# connection. DeleteItem is a no-op if the row is already gone (e.g. TTL swept
# it), so this is idempotent.
import os
 
import boto3
 
_TABLE = os.environ['WS_CONNECTIONS_TABLE']
 
_ddb = boto3.client('dynamodb')
 
 
def handler(event, context):
    connection_id = event['requestContext']['connectionId']
    _ddb.delete_item(
        TableName=_TABLE,
        Key={'connectionId': {'S': connection_id}},
    )
    return {'statusCode': 200}