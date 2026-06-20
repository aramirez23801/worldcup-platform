"""
World Cup agent: a Strands GraphBuilder graph wrapped as an AgentCore runtime.

A triage node classifies the user's request and conditional edges route it to
exactly one specialist. Knowledge answers questions from the football knowledge
base; Forecast predicts matches from Elo plus a Poisson goals model; Betting
previews and places fake-coin wagers on scheduled matches. AgentCore Memory
stores the conversation turns per session so follow-up questions keep their
context. A Bedrock guardrail screens each incoming user message for real
financial advice before the graph runs. The whole graph runs in this one
container; the entry point exposes it on POST /invocations.
"""

import logging
import os

import boto3
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole
from bedrock_agentcore.memory.session import MemorySessionManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.multiagent import GraphBuilder

from tools import (
    get_match_forecast,
    place_bet,
    query_team_knowledge,
    quote_bet,
    set_current_user,
)

# Emit this app's logs to stderr at INFO so they reach CloudWatch. Own handler on
# the package logger, no propagation, so there is no duplication with the runtime.
_wc = logging.getLogger("worldcup")
if not _wc.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    _wc.addHandler(_handler)
_wc.setLevel(logging.INFO)
_wc.propagate = False
logger = logging.getLogger("worldcup.agent")

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# Short-term conversation memory. Built only when the runtime provides the id, so
# the agent still runs without memory configured (for example locally).
_MEMORY_ID = os.environ.get("BEDROCK_AGENTCORE_MEMORY_ID")
_memory = MemorySessionManager(memory_id=_MEMORY_ID, region_name=_REGION) if _MEMORY_ID else None
_HISTORY_TURNS = 5

# Guardrail, applied as an explicit input check (see _input_block), not attached
# to the model. It blocks real financial or investment advice; the betting
# boundary (in-app fake coins versus real-money or external betting) is the
# specialists' job, not the guardrail. As an input check it sees only the user's
# current message, never model output or history, so it never fires on the
# forecaster's own fake-money odds or on those odds replayed from memory.
_GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID")
_GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT")
_bedrock = boto3.client("bedrock-runtime", region_name=_REGION)

# One plain model for every node. No guardrail attached anywhere.
model = BedrockModel(model_id=_MODEL_ID, region_name=_REGION)

# Appended to every user-facing specialist. The Ask page renders replies as plain
# text, so Markdown shows up literally (** , #, pipe tables). Force plain prose.
PLAIN_TEXT = " Format your reply as plain text, never Markdown. Do not use asterisks for emphasis (no ** and no *), do not use # headings, and do not use Markdown tables or pipe characters. When you need to present several values such as a bet quote, write them as short labelled lines, one per line (for example 'Match: Brazil vs Haiti'), not as a table. You may use a few emojis to keep replies friendly (for example a flag next to a team or an emoji on a key line), but use them sparingly, not on every line."

TRIAGE_PROMPT = "You are the router for a World Cup assistant. The input may begin with earlier conversation for context, followed by the current message to act on. Read the current message and decide which specialist should handle it. ROUTE=KNOWLEDGE for questions about national teams, their World Cup history, past results, standings, or head-to-head records; ROUTE=FORECAST for predicting a specific match, win/draw/loss probabilities, or odds for a matchup; ROUTE=BETTING for placing a wager with coins, asking which team or outcome to bet on, or confirming a bet. If the current message is a short follow-up that continues a previous request, for example confirming a quoted bet or naming another opponent after a prediction, route it the same way as that previous request. Output the current user message exactly, then on a new line output exactly one routing tag and nothing after it."

KNOWLEDGE_PROMPT = "You are the knowledge specialist for a World Cup assistant. Earlier conversation may be included for context. Answer questions about national teams, their World Cup history, past results, and head-to-head records by calling the query_team_knowledge tool, which searches a knowledge base of real historical matches. Base your answer on the tool's results and stay factual and concise. If the input ends with a line starting with ROUTE=, ignore that line."

FORECAST_PROMPT = "You are the forecast specialist for a World Cup assistant. Earlier conversation may be included for context; if the current message refers back to a previous match or team, infer the intended teams from it. To predict a match or give odds, call get_match_forecast with the two national team names, using exact names (for example 'United States', not 'USA'). Report the win/draw/loss (1X2) probabilities, the Over/Under 2.5 goals probabilities, and the fair decimal odds, and briefly mention the expected goals. This is a statistical model over fake-money markets, not real betting advice. If the input ends with a line starting with ROUTE=, ignore that line."

BETTING_PROMPT = "You are the betting specialist for a World Cup assistant. This is a fake-money game: users wager virtual coins, never real money, and there is no real betting. Earlier conversation may be included for context; if the current message refers back, infer the intended teams, pick, and stake from it. To place a bet, always quote it first: call quote_bet with the two team names, the pick (a team's name to win, 'draw', 'over 2.5', or 'under 2.5'), and the stake in coins, then tell the user the decimal odds, the potential return, and their resulting balance, and ask them to confirm. Only after the user explicitly confirms, call place_bet with the same details, and report the bet id and new balance. Never call place_bet before the user has confirmed a quote. If the user asks which team or outcome to bet on, call get_match_forecast for the matchup and suggest the selection that looks best value from the model's probabilities, making clear this is a statistical model that can be wrong, not financial advice. You only handle this app's fake-coin bets: do not recommend or discuss real-money betting, betting websites, or sportsbooks, and if asked say this is a virtual-coin game. Use exact national team names. If the input ends with a line starting with ROUTE=, ignore that line."

triage = Agent(name="triage", model=model, system_prompt=TRIAGE_PROMPT)
knowledge = Agent(
    name="knowledge",
    model=model,
    system_prompt=KNOWLEDGE_PROMPT + PLAIN_TEXT,
    tools=[query_team_knowledge],
)
forecast = Agent(
    name="forecast",
    model=model,
    system_prompt=FORECAST_PROMPT + PLAIN_TEXT,
    tools=[get_match_forecast],
)
betting = Agent(
    name="betting",
    model=model,
    system_prompt=BETTING_PROMPT + PLAIN_TEXT,
    tools=[get_match_forecast, quote_bet, place_bet],
)


def _triage_text(state):
    """Text the triage node produced, used by the routing conditions."""
    node_result = state.results.get("triage")
    if node_result is None:
        return ""
    return " ".join(str(ar) for ar in node_result.get_agent_results())


def route_forecast(state):
    return "ROUTE=FORECAST" in _triage_text(state)


def route_betting(state):
    return "ROUTE=BETTING" in _triage_text(state)


def route_knowledge(state):
    # Default specialist: anything not explicitly a forecast or a bet.
    text = _triage_text(state)
    return "ROUTE=FORECAST" not in text and "ROUTE=BETTING" not in text


_builder = GraphBuilder()
_builder.add_node(triage, "triage")
_builder.add_node(knowledge, "knowledge")
_builder.add_node(forecast, "forecast")
_builder.add_node(betting, "betting")
_builder.add_edge("triage", "forecast", condition=route_forecast)
_builder.add_edge("triage", "betting", condition=route_betting)
_builder.add_edge("triage", "knowledge", condition=route_knowledge)
_builder.set_entry_point("triage")
# Acyclic graph; a small cap is defensive and silences the generic cycle warning.
_builder.set_max_node_executions(10)
graph = _builder.build()

app = BedrockAgentCoreApp()


def _input_block(text):
    """Block message if the guardrail rejects this user input, else None."""
    if not _GUARDRAIL_ID or not text:
        return None
    try:
        resp = _bedrock.apply_guardrail(
            guardrailIdentifier=_GUARDRAIL_ID,
            guardrailVersion=_GUARDRAIL_VERSION,
            source="INPUT",
            content=[{"text": {"text": text}}],
        )
    except Exception:
        # Fail open: an infrastructure error in the check should not take the
        # assistant down. The error is logged for follow-up.
        logger.exception("guardrail check failed")
        return None
    if resp.get("action") != "GUARDRAIL_INTERVENED":
        return None
    outputs = resp.get("outputs") or []
    if outputs and outputs[0].get("text"):
        return outputs[0]["text"]
    return "Sorry, I can't help with that request."


def _recent_history(actor_id, session_id):
    """Recent turns for this session (both sides), as a context preamble."""
    if _memory is None:
        return ""
    try:
        turns = _memory.get_last_k_turns(actor_id=actor_id, session_id=session_id, k=_HISTORY_TURNS)
    except Exception:
        logger.exception("memory read failed")
        return ""
    lines = []
    for turn in turns:
        for msg in turn:
            role = (msg.get("role") or "").lower()
            content = msg.get("content") or {}
            text = content.get("text", "") if isinstance(content, dict) else str(content)
            if text:
                who = "User" if role == "user" else "Assistant"
                lines.append(f"{who}: {text}")
    logger.info("memory read: %d turn(s)", len(turns))
    if not lines:
        return ""
    return "Earlier in this conversation:\n" + "\n".join(lines) + "\n\n"


@app.entrypoint
def invoke(payload, context):
    """Run the graph and return the specialist's answer, with session memory."""
    prompt = payload.get("prompt", "")
    actor_id = payload.get("actor_id", "demo-user")
    session_id = getattr(context, "session_id", None) or "default-session"

    # Screen the user's message before doing anything else. A blocked message is
    # not run through the graph and not stored in memory.
    blocked = _input_block(prompt)
    if blocked is not None:
        logger.info("input blocked by guardrail")
        return {"response": blocked, "specialist": None}

    # The verified caller for this turn, for the betting tools to act on the right
    # wallet. Set out of band here, never from the model, so a user cannot bet from
    # someone else's wallet.
    set_current_user(actor_id)

    history = _recent_history(actor_id, session_id)
    task = f"{history}Current message: {prompt}" if history else prompt

    answer = ""
    specialist = None
    result = graph(task)
    if result.execution_order:
        last_id = result.execution_order[-1].node_id
        node_result = result.results.get(last_id)
        if node_result is not None:
            answers = node_result.get_agent_results()
            answer = "\n".join(str(a) for a in answers) if answers else ""
            specialist = last_id
    logger.info("routed to %s", specialist)

    if _memory is not None and prompt:
        try:
            _memory.add_turns(
                actor_id=actor_id,
                session_id=session_id,
                messages=[
                    ConversationalMessage(prompt, MessageRole.USER),
                    ConversationalMessage(answer or "(no answer)", MessageRole.ASSISTANT),
                ],
            )
        except Exception:
            logger.exception("memory write failed")

    return {"response": answer, "specialist": specialist}


if __name__ == "__main__":
    app.run()