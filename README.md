# World Cup Knowledge and Prediction Platform

A fully serverless, AWS-native web app for the 2026 World Cup. Three features for
signed-in users: a knowledge chatbot that answers questions about national teams
and their World Cup history, a statistical match forecaster, and a fake-money
betting game (every user starts with 10,000 coins; there is no real money). Live
scores during the tournament settle bets automatically and move a realized-profit
leaderboard in real time. Everything is built and deployed with AWS CDK in Python.

## Layout

The CDK app is one Python project. `app.py` wires seven stacks together, each one
owning a clear slice of the system.

- `app.py`: CDK app entry point; creates the stacks and passes shared resources between them.
- `cdk.json`: CDK CLI config and feature flags.
- `requirements.txt`: CDK Python dependencies.
- `stacks/data.py`: the seven DynamoDB tables, their indexes, and the deploy-time seeding of fixtures and Elo ratings.
- `stacks/auth.py`: Cognito user pool, hosted sign-in UI, the SPA app client, and a trigger that gives each new user a starting wallet.
- `stacks/knowledge.py`: the Bedrock knowledge base over the World Cup corpus (stored in S3 Vectors) and the Bedrock guardrail.
- `stacks/agent.py`: the chatbot agent, packaged as a container and run on Bedrock AgentCore.
- `stacks/api.py`: the REST API (with a Cognito authorizer) and the WebSocket API for live updates, in front of per-concern Lambdas.
- `stacks/settlement.py`: the live-results poller, the bet-settlement pipeline, the leaderboard aggregator, and the CloudWatch alarms.
- `stacks/frontend.py`: the React site on a private S3 bucket behind CloudFront, with a web application firewall in front.
- `assets/_lambda/`: one folder per Lambda (API handlers, the settlement workflow, the results poller, the WebSocket handlers, and the deploy-time seeders).
- `assets/agent/`: the agent container (the Strands graph, its tools, and the Dockerfile).
- `assets/domain/`: shared Python the Lambdas and the agent both use (forecasting, betting, wallet, settlement, leaderboard), shipped as a Lambda layer.
- `assets/kb_corpus/`: the markdown corpus the knowledge base ingests (per-country profiles and head-to-head records).
- `assets/seed/`: the committed fixtures, schedule, and baseline Elo ratings used to seed the tables at deploy.
- `frontend/`: the React single-page app (Vite and TypeScript).

## How it works

The platform is event-driven and splits cleanly into four areas.

### Auth and identity

Sign-in runs on a Cognito user pool with its hosted login UI. The single-page app
uses the authorization-code flow, so after login it holds tokens that authorize
every API call. The pool collects only an email and password. When a user confirms
their account, a Cognito trigger runs a small Lambda that seeds them a wallet with
the starting balance, so a new player can bet immediately.

### Data and API

Seven DynamoDB tables hold the state: Wallets, Matches, Bets, Teams, Notifications,
Leaderboard, and WsConnections. All of them use on-demand billing and AWS-managed
encryption. The tables that need a second access pattern carry a secondary index
(for example, loading every bet on a match by status during settlement, or finding
a user's live connections to push them an update). Wallets and Bets also have
point-in-time recovery turned on, since they hold the money-relevant records.

The REST API is API Gateway with a Cognito authorizer on every route, so each
request is checked against the user pool before it reaches a Lambda. The user's id
is taken from the verified token, never from the request body, so a caller can only
ever read or change their own data. Each Lambda has its own role scoped to just the
tables and actions it uses. Read routes serve matches, the bracket, the wallet, the
user's bets, the forecast, and the leaderboard; write routes place a bet and set a
leaderboard display name.

A second API is a WebSocket API. The single-page app opens one connection after
login (the connection is authorized with the user's token), and the server pushes
updates down it with no client request: live scores, settled-bet outcomes, wallet
changes, and leaderboard moves. The connection ids live in the WsConnections table
so the backend can find who to push to.

### The agent

The chatbot is a multi-agent graph built with the Strands SDK and run on Amazon
Bedrock AgentCore as a container. A triage step reads the message and routes it to
one of three specialists: knowledge (answers from the knowledge base), forecast
(runs the same forecasting code the rest of the app uses), and betting (quotes and
places fake-coin bets). The agent keeps short-term memory per session so follow-up
questions keep their context. Before the graph runs, each user message is screened
by a Bedrock guardrail that blocks requests for real financial or investment advice,
since this is a fake-money game and not a real betting or trading product. The
betting tool the agent calls is the same shared code the REST API uses, so a bet
placed by chat and a bet placed by button behave identically.

The knowledge base is retrieval-augmented generation over a corpus of real World
Cup history. The corpus (per-country profiles and head-to-head records) is built
from a committed historical match dataset, shipped to an S3 bucket, and ingested
into a vector store (S3 Vectors) using Cohere embeddings. Because the source data
is committed to the repo, the build is deterministic and needs no network at deploy.
Re-ingestion happens automatically when the corpus changes.

### Live results and settlement

This is the asynchronous spine of the app. An EventBridge schedule runs a results
poller every minute. The poller reads a live football data feed (its token is held
in Secrets Manager), updates the Matches table, writes the running score and pushes
it over the WebSocket while a match is live, and when a match finishes it puts a
"match settled" event on a custom EventBridge bus.

That event is routed into an SQS queue, which drives the settlement workflow. The
settlement function is a Lambda durable function, so it processes one match at a
time and survives restarts without losing its place. It loads every pending bet on
the match, settles each one with a conditional write so a bet can never be paid
twice, credits the winners' wallets, recomputes both teams' Elo ratings from the
result so the forecaster reflects current form, and then hands the settled bets to a
notify function that pushes the outcomes and the new balances over the WebSocket. Any
message that fails settlement after its retries lands in a dead-letter queue, which
has its own alarm.

Separately, a stream on the Bets table drives a leaderboard aggregator. Whenever a
bet settles, the aggregator adds that bet's realized profit to the player's standing
and broadcasts the refreshed leaderboard to every connected client. The board is
ranked by realized profit, so it always agrees with the wallets.

## Prereqs

- AWS credentials configured (`aws configure` or environment variables).
- Docker running. The agent ships as a container image that CDK builds and pushes during deploy, so Docker must be available.
- Node.js and the CDK CLI: `npm install -g aws-cdk`.
- Python 3.12 or newer.
- Node.js and npm for the frontend build (the React app is built before deploy).

### Bedrock model access (do this first)

The agent runs on a Bedrock model that must be enabled in the deploying account
before first use. For Anthropic models, a first-time user may need to request access
in the Bedrock console once; until then the agent's calls fail with an access-denied
error that mentions AWS Marketplace. Open the model in the Bedrock console (or send
it one message in the playground) to enable it account-wide, then deploy.

The knowledge base embeds with a Cohere model and the agent generates with an
Anthropic model, so both providers must be accessible in the account's region
(us-east-1).

## Deploy

The site runs behind a CloudFront distribution whose domain name is generated by
AWS and is not known until the distribution exists. Cognito needs that exact domain
in its allowed sign-in and sign-out URLs. For that reason the deploy is two passes:
the first creates everything and prints the CloudFront URL, and the second passes
that URL back in so Cognito accepts logins from the live site.

First, build the frontend and install the CDK dependencies:

```bash
cd frontend && npm install && npm run build && cd ..
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap   # only needed once per account/region
```

First pass, deploy everything:

```bash
cdk deploy --all
```

Read the CloudFront URL from the `WorldCupFrontend` outputs (the `SiteUrl` value,
it looks like `https://dxxxxxxxxxxxxx.cloudfront.net`).

Second pass, deploy again with that URL so Cognito registers the live callback and
logout URLs:

```bash
cdk deploy --all -c frontend_url=https://YOUR-CLOUDFRONT-URL
```

This second command is required. Without it, sign-in and sign-out from the live site
fail, because Cognito only has the local development URL in its allowed list. Any
later deploy that touches the auth stack must also pass `-c frontend_url=...`, or the
allowed list reverts to local-only.

A custom domain is the production answer to this. With a domain (Route 53 and a
certificate) the site URL is known in advance, the callback can be set on the first
deploy, and the two passes collapse into one.

## Post-deploy setup

The results poller reads a football data feed and needs an API token. The deploy
creates an empty secret named `worldcup/football-data-token`; put a real token in
it once. A free token works for the demo (it serves scores on a short delay, which
is expected). Get one at https://www.football-data.org and set the value:

```bash
aws secretsmanager put-secret-value \
  --secret-id worldcup/football-data-token \
  --secret-string "YOUR-FOOTBALL-DATA-TOKEN" \
  --region us-east-1
```

The tables are seeded automatically at deploy: the full schedule of fixtures lands
in the Matches table and baseline Elo ratings land in the Teams table, both from
committed seed files, so the app has data the moment it comes up. The Elo ratings are
a starting point, not a fixed value: they update as knockout matches settle during
the tournament. Live scores and the resolved knockout teams fill in from the feed.

## Architecture notes

### Exactly-once settlement

A bet must never be paid twice. The settlement function is a durable function fed
one match at a time from SQS, and it settles each bet with a conditional write that
only pays a bet still marked pending. If the workflow restarts or a message is
redelivered, an already-settled bet is skipped, so the money path is exactly-once
even though the surrounding delivery is at-least-once.

### Runtime configuration, not build-time

The frontend's settings (the API URL, the WebSocket URL, the Cognito details) are
not known until the stacks deploy, which is after the React app has already been
built. So they are not baked into the build. Instead the frontend stack writes a
small config file to the site at deploy time, and the app reads it at startup. The
build carries no environment and runs unchanged in any account.

### Least privilege

Every Lambda role is scoped to the exact tables and actions it touches, and the IAM
is hand-written rather than relying on broad helper grants, so each function can do
only its own job. The display-name setter, for example, can update the Leaderboard
table and nothing else.

### Statistical forecasting, not machine learning

The forecaster is a transparent statistical model (team strength plus a goals model)
rather than a trained machine-learning model. It is explainable and needs no training
pipeline. Team strength is an Elo rating: at deploy the full match history is replayed
in date order to produce a baseline rating per team, weighting matches by importance
(World Cup results count most, friendlies least) and by goal margin, so recent form
naturally dominates. The ratings then keep updating during the tournament: when a
match settles, the settlement step recomputes both teams' Elo from the result and
writes it back, so each new forecast reflects current-tournament form. Because the
committed history already covers the group stage, the live updates apply to the
knockout results the baseline has not already absorbed, which avoids counting a match
twice.

### Notifications are real-time, not email

When a bet settles, the user is told over the live WebSocket connection (their
wallet and the result update on screen). The platform does not send settlement
emails. Reliable email would require taking a verified sender out of the email
service's sandbox, which is account setup unrelated to the architecture, so the
live push is the single notification channel.

### Account-agnostic

`app.py` reads the account and region from the environment with no hardcoded account
id, and every table, queue, and resource name is either generated or stable, so
`cdk deploy` works in any account.

## Monitoring

Monitoring is deliberately small: a few alarms that each catch a real, silent
failure, with no noise. They are visible in the CloudWatch console (there is no
paging set up, by choice).

- A dead-letter-queue alarm: fires if a bet fails settlement after all retries and parks in the dead-letter queue.
- Four Lambda error alarms, one each on the settlement, results, leaderboard, and notify functions: fire if any backbone function starts failing.
- A feed-outage alarm: fires only if the results poller cannot reach the live feed for several minutes in a row. A delayed feed (normal on the free tier) is a successful fetch and does not trip it; only a real outage or a bad token does.

## Teardown

```bash
cdk destroy --all
```

Every resource is set to remove cleanly: the tables, queues, log groups, the docs
bucket, and the vector store all delete, so the stack tears down without manual
cleanup. The CDK bootstrap stack stays in the account.

## Troubleshooting

### Sign-in or sign-out fails on the live site

A deploy re-synthesized the auth stack without the CloudFront URL, so Cognito lost
the live callback and logout URLs. Re-run the deploy with the flag:
`cdk deploy --all -c frontend_url=https://YOUR-CLOUDFRONT-URL`. Confirm the URLs are
back with `aws cognito-idp describe-user-pool-client` (the callback and logout lists
should contain the CloudFront URL alongside the local one).

### The chatbot returns a server error on every message

The Bedrock model is not enabled in the account. Open it in the Bedrock console (or
send it one message in the playground) to enable it account-wide, then try again. The
underlying error mentions AWS Marketplace access.

### Scores are not updating

Check the football data token is set in the `worldcup/football-data-token` secret. If
it is set, a delay is expected on the free tier (the feed serves scores a few minutes
behind play). A real outage shows up on the feed-outage alarm.

### The login page looks unstyled on a fresh deploy

The login UI uses Cognito's managed login. The auth stack already attaches a branding
resource that pins Cognito's default styling, which handles this; if a fresh deploy
ever shows an unstyled page, redeploying the auth stack reapplies it.
