import json
import os
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError

from domain import http

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]

# The agent runs an LLM graph, so allow a generous read timeout, but keep it just
# under API Gateway's 29s integration limit so a slow answer returns a clean 504
# instead of being cut off (streaming over WebSocket is the later refinement).
_agent = boto3.client(
    "bedrock-agentcore",
    region_name=_REGION,
    config=Config(read_timeout=28, connect_timeout=10, retries={"max_attempts": 1}),
)


def _new_session_id():
    # AgentCore requires a runtimeSessionId of at least 33 characters; two hex
    # uuids give 64. The client reuses the returned id to keep the conversation's
    # memory continuity across turns.
    return uuid.uuid4().hex + uuid.uuid4().hex


def handler(event, context):
    user_id = http.user_id(event)
    if not user_id:
        return http.response(401, {"error": "unauthenticated"})

    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, ValueError):
        return http.response(400, {"error": "invalid JSON body"})

    message = body.get("message")
    if not message:
        return http.response(400, {"error": "missing field: message"})

    session_id = body.get("sessionId") or _new_session_id()

    try:
        resp = _agent.invoke_agent_runtime(
            agentRuntimeArn=_RUNTIME_ARN,
            runtimeSessionId=session_id,
            contentType="application/json",
            accept="application/json",
            # actor_id is the verified caller, so each user's memory is separate.
            payload=json.dumps({"prompt": message, "actor_id": user_id}).encode("utf-8"),
        )
        raw = resp["response"].read()
    except ReadTimeoutError:
        return http.response(504, {"error": "the assistant took too long, please try again"})
    except ClientError as exc:
        print("agent invoke failed:", exc)
        return http.response(502, {"error": "assistant unavailable"})

    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        print("agent returned non-JSON:", raw[:500])
        return http.response(502, {"error": "unexpected response from assistant"})

    return http.response(200, {
        "sessionId": session_id,
        "specialist": data.get("specialist"),
        "response": data.get("response"),
    })