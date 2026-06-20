"""Wallet reads and writes: balance, atomic debit, credit."""
 
import os
from datetime import datetime, timezone
 
import boto3
from botocore.exceptions import ClientError
 
_REGION = os.environ.get("AWS_REGION", "us-east-1")
_ddb = boto3.resource("dynamodb", region_name=_REGION)
_wallets = _ddb.Table(os.environ.get("WALLETS_TABLE", "Wallets"))
 
 
class InsufficientFunds(Exception):
    """A debit would take the balance below zero (or the wallet is missing)."""
 
 
def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
 
 
def get_balance(user_id):
    """Current balance as an int, or None if the user has no wallet."""
    item = _wallets.get_item(Key={"userId": user_id}).get("Item")
    return int(item["balance"]) if item else None
 
 
def debit(user_id, amount):
    """Atomically subtract amount when the balance covers it; returns the new
    balance. Raises InsufficientFunds if the balance is too low. The condition
    and update are a single DynamoDB write, so concurrent debits cannot overspend.
    """
    try:
        resp = _wallets.update_item(
            Key={"userId": user_id},
            UpdateExpression="SET balance = balance - :amt, updatedAt = :now",
            ConditionExpression="attribute_exists(userId) AND balance >= :amt",
            ExpressionAttributeValues={":amt": amount, ":now": _now()},
            ReturnValues="UPDATED_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise InsufficientFunds(f"balance does not cover {amount}")
        raise
    return int(resp["Attributes"]["balance"])
 
 
def credit(user_id, amount):
    """Add amount to an existing wallet; returns the new balance."""
    resp = _wallets.update_item(
        Key={"userId": user_id},
        UpdateExpression="SET balance = balance + :amt, updatedAt = :now",
        ConditionExpression="attribute_exists(userId)",
        ExpressionAttributeValues={":amt": amount, ":now": _now()},
        ReturnValues="UPDATED_NEW",
    )
    return int(resp["Attributes"]["balance"])