import os
import boto3

ddb = boto3.resource('dynamodb')
table = ddb.Table(os.environ['WALLETS_TABLE'])

STARTING_BALANCE = int(os.environ['STARTING_BALANCE'])


def seed_wallet(user_id):
    # Conditional put so an existing wallet is never overwritten. Makes the seed idempotent.
    table.put_item(
        Item={'userId': user_id, 'balance': STARTING_BALANCE},
        ConditionExpression='attribute_not_exists(userId)',
    )


def handler(event, context):
    # Only seed on a real sign-up confirmation, not on the forgot-password flow.
    if event.get('triggerSource') != 'PostConfirmation_ConfirmSignUp':
        return event

    user_id = event['request']['userAttributes']['sub']

    try:
        seed_wallet(user_id)
    except ddb.meta.client.exceptions.ConditionalCheckFailedException:
        # Wallet already exists, nothing to do.
        pass

    # Cognito requires the trigger to return the event.
    return event
