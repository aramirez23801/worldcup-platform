import os
 
import boto3
 
ddb = boto3.resource("dynamodb")
table = ddb.Table(os.environ["MATCHES_TABLE"])
 
# CloudFormation passes custom-resource property values as strings, so numeric fields are cast back. Everything else is already a string.
_INT_FIELDS = {"matchday"}
 
 
def handler(event, context):
    if event.get("RequestType") == "Delete":
        # Tables are torn down with the stack; nothing to undo here.
        return {"PhysicalResourceId": event.get("PhysicalResourceId", "matches-seed")}
 
    matches = event["ResourceProperties"]["matches"]
    with table.batch_writer() as batch:
        for m in matches:
            item = {}
            for key, value in m.items():
                if value is None or value == "":
                    continue
                item[key] = int(value) if key in _INT_FIELDS else value
            batch.put_item(Item=item)
    return {"PhysicalResourceId": "matches-seed", "Data": {"seeded": str(len(matches))}}