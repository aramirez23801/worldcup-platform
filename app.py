import aws_cdk as cdk

from stacks import *

app = cdk.App()

data = DataStack(app, "WorldCupData")

# Cognito callback/logout URLs: localhost for dev always; the CloudFront URL is
# added only when you pass it, after the frontend is deployed:
#   cdk deploy WorldCupAuth WorldCupFrontend -c frontend_url=https://xxxx.cloudfront.net
frontend_url = app.node.try_get_context("frontend_url")
callback_urls = ["http://localhost:5173/callback"]
logout_urls = ["http://localhost:5173"]
if frontend_url:
    callback_urls.append(f"{frontend_url}/callback")
    logout_urls.append(frontend_url)

auth = AuthStack(
    app, "WorldCupAuth",
    wallets_table=data.wallets,
    callback_urls=callback_urls,
    logout_urls=logout_urls,
)

knowledge = KnowledgeStack(app, "WorldCupKnowledge")

agent = AgentStack(
    app, "WorldCupAgent",
    teams_table=data.teams,
    bets_table=data.bets,
    wallets_table=data.wallets,
    matches_table=data.matches,
    knowledge_base_id=knowledge.knowledge_base_id,
    knowledge_base_arn=knowledge.knowledge_base_arn,
    guardrail_id=knowledge.guardrail_id,
    guardrail_arn=knowledge.guardrail_arn,
    guardrail_version=knowledge.guardrail_version,
)

api = ApiStack(app, "WorldCupApi",
    user_pool=auth.user_pool,
    user_pool_client=auth.user_pool_client,
    matches_table=data.matches,
    wallets_table=data.wallets,
    teams_table=data.teams,
    bets_table=data.bets,
    ws_connections_table=data.ws_connections,
    leaderboard_table=data.leaderboard,
    agent_runtime_arn=agent.runtime_arn,
)

SettlementStack(app, "WorldCupSettlement",
    bets_table=data.bets,
    wallets_table=data.wallets,
    matches_table=data.matches,
    teams_table=data.teams,
    leaderboard_table=data.leaderboard,
    ws_api=api.ws_api,
    ws_stage=api.ws_stage,
    ws_connections_table=data.ws_connections,
)

FrontendStack(app, "WorldCupFrontend",
    api_url=api.api.url,
    ws_url=api.ws_stage.url,
    user_pool=auth.user_pool,
    user_pool_client=auth.user_pool_client,
    cognito_domain=auth.hosted_ui_domain,
)

app.synth()