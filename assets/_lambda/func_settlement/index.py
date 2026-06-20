"""Settlement durable function.

Triggered by one match.settled message at a time (SQS event source, batch size 1).
Loads the match's still-PENDING bets and settles each one as a durable step. The
load is itself a step so a replay settles exactly the set it started with, and each
per-bet settle is idempotent, so a replayed or redelivered settlement never double
pays.

After settling, the bets that actually changed on this run are handed to the Notify
function through an asynchronous invoke wrapped in a checkpointed step. The step
makes that invoke fire exactly once across replays; the Event invocation type keeps
settlement decoupled from notification delivery, which is best-effort. A redelivery
settles nothing new, so it notifies nothing.
"""

import json
import os

import boto3

from aws_durable_execution_sdk_python import (
    DurableContext,
    StepContext,
    durable_execution,
    durable_step,
)

from domain import settlement

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_lambda_client = boto3.client("lambda", region_name=_REGION)
_NOTIFY_FUNCTION = os.environ["NOTIFY_FUNCTION"]


@durable_step
def load_pending(step_context: StepContext, match_id):
    """Checkpointed read: pins the bet set so a replay does not re-query a changed
    table and fall out of step with its checkpoints."""
    return settlement.pending_bets_for_match(match_id)


@durable_step
def settle_one(step_context: StepContext, bet, match):
    """Settle a single bet. Idempotent at the data layer, so replaying this step is
    a safe no-op once the bet is no longer PENDING."""
    return settlement.settle_bet(bet, match)


@durable_step
def fire_notify(step_context: StepContext, payload):
    """Hand the run's settled bets to Notify. Asynchronous so a slow or failing
    notifier never holds up or fails settlement; in a step so it fires once."""
    _lambda_client.invoke(
        FunctionName=_NOTIFY_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    return True


@durable_execution
def handler(event, context: DurableContext):
    # SQS event source is configured batch size 1, so exactly one record: the
    # EventBridge envelope, with the match result under "detail".
    record = event["Records"][0]
    detail = json.loads(record["body"])["detail"]
    match_id = detail["matchId"]
    score_home = detail["scoreHome"]
    score_away = detail["scoreAway"]
    match = {"scoreHome": score_home, "scoreAway": score_away}

    bets = context.step(load_pending(match_id), name="load-pending")

    settled = []
    for bet in bets:
        result = context.step(settle_one(bet, match), name="settle-" + bet["betId"])
        if result["settled"]:
            settled.append({
                "userId": bet["userId"],
                "betId": bet["betId"],
                "betType": bet["betType"],
                "selection": bet["selection"],
                "status": result["status"],
                "payout": int(result["payout"]),
            })

    if settled:
        context.step(
            fire_notify({
                "matchId": match_id,
                "scoreHome": score_home,
                "scoreAway": score_away,
                "settlements": settled,
            }),
            name="notify",
        )

    return {"matchId": match_id, "bets": len(bets), "settled": len(settled)}