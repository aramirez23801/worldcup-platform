
import os
 
import boto3
 
ddb = boto3.resource("dynamodb")
table = ddb.Table(os.environ["TEAMS_TABLE"])
 
 
def handler(event, context):
    if event.get("RequestType") == "Delete":
        return {"PhysicalResourceId": event.get("PhysicalResourceId", "elo-seed")}
 
    teams = event["ResourceProperties"]["teams"]
    with table.batch_writer() as batch:
        for team in teams:
            batch.put_item(
                Item={
                    "teamId": str(team["teamId"]),
                    "eloRating": int(team["eloRating"]),
                    "matches": int(team["matches"]),
                }
            )
    return {"PhysicalResourceId": "elo-seed", "Data": {"seeded": str(len(teams))}}