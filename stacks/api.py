import os

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_apigateway as apig,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_int,
    aws_apigatewayv2_authorizers as apigwv2_auth,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
)
from constructs import Construct


class ApiStack(Stack):
    """
    REST API for the single-page app. API Gateway with a Cognito authorizer in
    front of per-concern Lambdas, each with a role scoped to just the tables it
    touches. The Lambdas share the domain package through a layer, so they run the
    same forecast, wallet, and betting code as the agent.
    """
    _RUNTIME = _lambda.Runtime.PYTHON_3_14
    _ARCH = _lambda.Architecture.ARM_64

    def __init__(self, scope: Construct, construct_id: str, *,
                 user_pool, user_pool_client,
                 matches_table, wallets_table, teams_table, bets_table, ws_connections_table, leaderboard_table,
                 agent_runtime_arn, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ------------- SHARED DOMAIN LAYER ------------- #
        # assets/domain holds python/domain/, so the layer puts the package on the
        # Lambda path: `from domain import matches`. Pure standard library plus
        # boto3 (already in the runtime), so there is nothing to pip install.
        self.domain_layer = _lambda.LayerVersion(
            self, 'DomainLayer',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', 'domain'),
                exclude=['**/__pycache__', '**/*.pyc'],
            ),
            compatible_runtimes=[self._RUNTIME],
            compatible_architectures=[self._ARCH],
            description='Shared World Cup domain logic (forecast, matches, wallet, betting, standings).',
        )

        # ------------- REST API ------------- #
        self.api = apig.RestApi(
            self, 'WorldCupApi',
            rest_api_name='worldcup-api',
            deploy_options=apig.StageOptions(stage_name='prod'),
            cloud_watch_role=False,
            default_cors_preflight_options=apig.CorsOptions(
                allow_origins=apig.Cors.ALL_ORIGINS,
                allow_methods=apig.Cors.ALL_METHODS,
                allow_headers=['Content-Type', 'Authorization'],
            ),
        )

        # Cognito authorizer: every method below validates the user pool JWT, and
        # the user's id arrives in the request context as the token's `sub` claim.
        self.authorizer = apig.CognitoUserPoolsAuthorizer(
            self, 'Authorizer',
            cognito_user_pools=[user_pool],
        )
        authed = {
            'authorizer': self.authorizer,
            'authorization_type': apig.AuthorizationType.COGNITO,
        }

        # ------------- MATCHES READS (func_matches) ------------- #
        # One Lambda owns the read-only match views: list, single match, group
        # standings, and the bracket. Role scoped to read the Matches table only.
        self.matches_logs = logs.LogGroup(
            self, 'MatchesFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.matches_fn = _lambda.Function(
            self, 'MatchesFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_matches')
            ),
            layers=[self.domain_layer],
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.matches_logs,
            environment={'MATCHES_TABLE': matches_table.table_name},
        )
        self.matches_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem', 'dynamodb:Query', 'dynamodb:Scan'],
            resources=[
                matches_table.table_arn,
                f'{matches_table.table_arn}/index/gsi_status_kickoff',
            ],
        ))

        # Match read routes, all served by func_matches.
        integration = apig.LambdaIntegration(self.matches_fn)
        matches_res = self.api.root.add_resource('matches')
        matches_res.add_method('GET', integration, **authed)          # GET /matches
        matches_res.add_resource('{id}').add_method('GET', integration, **authed)  # GET /matches/{id}
        self.api.root.add_resource('standings').add_method('GET', integration, **authed)  # GET /standings
        self.api.root.add_resource('bracket').add_method('GET', integration, **authed)    # GET /bracket

        # ------------- WALLET (func_wallet) ------------- #
        # Per-user read. The user id comes from the verified token claim, never
        # from the request. Role scoped to read the Wallets table only.
        self.wallet_logs = logs.LogGroup(
            self, 'WalletFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.wallet_fn = _lambda.Function(
            self, 'WalletFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_wallet')
            ),
            layers=[self.domain_layer],
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.wallet_logs,
            environment={'WALLETS_TABLE': wallets_table.table_name},
        )
        self.wallet_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem'],
            resources=[wallets_table.table_arn],
        ))
        self.api.root.add_resource('wallet').add_method(
            'GET', apig.LambdaIntegration(self.wallet_fn), **authed)  # GET /wallet

        # ------------- BETS (func_bets) ------------- #
        # GET lists the caller's bets; POST places one. place_bet reads Matches and
        # Teams to price the bet, then debits Wallets and writes Bets in one
        # transaction. Role scoped per table to exactly the actions it needs.
        self.bets_logs = logs.LogGroup(
            self, 'BetsFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.bets_fn = _lambda.Function(
            self, 'BetsFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_bets')
            ),
            layers=[self.domain_layer],
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.bets_logs,
            environment={
                'MATCHES_TABLE': matches_table.table_name,
                'TEAMS_TABLE': teams_table.table_name,
                'WALLETS_TABLE': wallets_table.table_name,
                'BETS_TABLE': bets_table.table_name,
            },
        )
        # Matches and Teams read for pricing, Wallets debit, Bets write and list.
        self.bets_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem'], resources=[matches_table.table_arn]))
        self.bets_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem'], resources=[teams_table.table_arn]))
        self.bets_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:UpdateItem'], resources=[wallets_table.table_arn]))
        self.bets_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:PutItem', 'dynamodb:Query'], resources=[bets_table.table_arn]))

        bets_res = self.api.root.add_resource('bets')
        bets_res.add_method('GET', apig.LambdaIntegration(self.bets_fn), **authed)   # GET /bets
        bets_res.add_method('POST', apig.LambdaIntegration(self.bets_fn), **authed)  # POST /bets

        # ------------- FORECAST (func_forecast) ------------- #
        # The button forecast: read the match for its two teams, then run the same
        # forecast_match the agent uses. Role scoped to read Matches and Teams.
        self.forecast_logs = logs.LogGroup(
            self, 'ForecastFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.forecast_fn = _lambda.Function(
            self, 'ForecastFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_forecast')
            ),
            layers=[self.domain_layer],
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.forecast_logs,
            environment={
                'MATCHES_TABLE': matches_table.table_name,
                'TEAMS_TABLE': teams_table.table_name,
            },
        )
        self.forecast_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem'], resources=[matches_table.table_arn]))
        self.forecast_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem'], resources=[teams_table.table_arn]))
        self.api.root.add_resource('forecast').add_resource('{id}').add_method(
            'GET', apig.LambdaIntegration(self.forecast_fn), **authed)  # GET /forecast/{id}
        
        # ------------- LEADERBOARD (func_leaderboard) ------------- #
        # Read the tournament leaderboard, players ordered by realized profit. The
        # aggregator (Bets stream) maintains the table; this just reads it. Role
        # scoped to query the Leaderboard table only.
        self.leaderboard_logs = logs.LogGroup(
            self, 'LeaderboardFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.leaderboard_fn = _lambda.Function(
            self, 'LeaderboardFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_leaderboard')
            ),
            layers=[self.domain_layer],
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.leaderboard_logs,
            environment={'LEADERBOARD_TABLE': leaderboard_table.table_name},
        )
        self.leaderboard_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:Query'],
            resources=[leaderboard_table.table_arn],
        ))
        leaderboard_res = self.api.root.add_resource('leaderboard')
        leaderboard_res.add_method(
            'GET', apig.LambdaIntegration(self.leaderboard_fn), **authed)  # GET /leaderboard

        # ------------- LEADERBOARD DISPLAY NAME (func_set_display_name) ------------- #
        # Lets the caller set their own leaderboard display name. The user id comes
        # from the verified token claim (never the body), so a user can only rename
        # themselves. Upserts only the displayName attribute onto their row, so it
        # never disturbs the profit/wins/losses the aggregator maintains. Role scoped
        # to update the Leaderboard table only.
        self.leaderboard_name_logs = logs.LogGroup(
            self, 'LeaderboardNameFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.leaderboard_name_fn = _lambda.Function(
            self, 'LeaderboardNameFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_set_display_name')
            ),
            layers=[self.domain_layer],
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.leaderboard_name_logs,
            environment={'LEADERBOARD_TABLE': leaderboard_table.table_name},
        )
        self.leaderboard_name_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:UpdateItem'],
            resources=[leaderboard_table.table_arn],
        ))
        leaderboard_res.add_resource('name').add_method(
            'PUT', apig.LambdaIntegration(self.leaderboard_name_fn), **authed)  # PUT /leaderboard/name

        # ------------- AGENT (func_agent) ------------- #
        # Synchronous invoker for the AgentCore runtime. No domain layer: it only
        # forwards the user's message (with their id as the memory actor) and
        # returns the agent's answer. Role scoped to invoke this one runtime.
        self.agent_logs = logs.LogGroup(
            self, 'AgentFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.agent_fn = _lambda.Function(
            self, 'AgentFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_agent')
            ),
            layers=[self.domain_layer],
            timeout=Duration.seconds(30),
            memory_size=256,
            log_group=self.agent_logs,
            environment={'AGENT_RUNTIME_ARN': agent_runtime_arn},
        )
        self.agent_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['bedrock-agentcore:InvokeAgentRuntime'],
            resources=[agent_runtime_arn, f'{agent_runtime_arn}/*'],
        ))
        self.api.root.add_resource('agent').add_method(
            'POST', apig.LambdaIntegration(self.agent_fn), **authed)  # POST /agent

        # ------------- WEBSOCKET API ------------- #
        # The SPA opens one persistent connection after login. Server-side events
        # (a match settling, a wallet change, a leaderboard move in Phase 6) push
        # down these connections with no client request. This block is the
        # plumbing: a JWT-authorized $connect that records the connection, and a
        # $disconnect that clears it. The $default route and the push helper that
        # settlement will call come in the next step.

        # python-jose layer for the $connect authorizer. Pure-Python (rsa backend),
        # so like the domain layer there is nothing platform-specific to build.
        self.jwt_layer = _lambda.LayerVersion(
            self, 'JwtLayer',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', 'jwt'),
                exclude=['**/__pycache__', '**/*.pyc', '**/test_*.py'],
            ),
            compatible_runtimes=[self._RUNTIME],
            compatible_architectures=[self._ARCH],
            description='python-jose for verifying Cognito JWTs in the WebSocket authorizer.',
        )

        # $connect authorizer: verifies the Cognito ID token (passed on the query
        # string, since a browser cannot set headers on the handshake) against the
        # pool JWKS, and hands the user's sub to $connect. It makes no AWS calls, so
        # it gets no role beyond logging; it only needs the pool and client ids.
        self.ws_authorizer_logs = logs.LogGroup(
            self, 'WsAuthorizerFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.ws_authorizer_fn = _lambda.Function(
            self, 'WsAuthorizerFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_ws_authorizer')
            ),
            layers=[self.jwt_layer],
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.ws_authorizer_logs,
            environment={
                'USER_POOL_ID': user_pool.user_pool_id,
                'CLIENT_ID': user_pool_client.user_pool_client_id,
            },
        )

        # $connect: record the live connection keyed to the user. Scoped to put.
        self.ws_connect_logs = logs.LogGroup(
            self, 'WsConnectFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.ws_connect_fn = _lambda.Function(
            self, 'WsConnectFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_ws_connect')
            ),
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.ws_connect_logs,
            environment={'WS_CONNECTIONS_TABLE': ws_connections_table.table_name},
        )
        self.ws_connect_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:PutItem'], resources=[ws_connections_table.table_arn]))

        # $disconnect: clear the connection row. Scoped to delete.
        self.ws_disconnect_logs = logs.LogGroup(
            self, 'WsDisconnectFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.ws_disconnect_fn = _lambda.Function(
            self, 'WsDisconnectFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_ws_disconnect')
            ),
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.ws_disconnect_logs,
            environment={'WS_CONNECTIONS_TABLE': ws_connections_table.table_name},
        )
        self.ws_disconnect_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:DeleteItem'], resources=[ws_connections_table.table_arn]))

        # $default: keepalive and any other client message. Replies pong on the
        # same connection through the push helper, so it needs the domain layer
        # and (added after the API exists) permission to post to connections.
        self.ws_default_logs = logs.LogGroup(
            self, 'WsDefaultFnLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.ws_default_fn = _lambda.Function(
            self, 'WsDefaultFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_ws_default')
            ),
            layers=[self.domain_layer],
            timeout=Duration.seconds(10),
            memory_size=256,
            log_group=self.ws_default_logs,
        )

        # The API: $connect (authorized) and $disconnect routes wired to the
        # Lambdas. The integration and authorizer constructs grant API Gateway
        # permission to invoke each Lambda, so there is no manual invoke grant here.
        ws_authorizer = apigwv2_auth.WebSocketLambdaAuthorizer(
            'WsAuthorizer',
            self.ws_authorizer_fn,
            identity_source=['route.request.querystring.token'],
        )
        self.ws_api = apigwv2.WebSocketApi(
            self, 'WorldCupWsApi',
            api_name='worldcup-ws',
            connect_route_options=apigwv2.WebSocketRouteOptions(
                integration=apigwv2_int.WebSocketLambdaIntegration(
                    'ConnectIntegration', self.ws_connect_fn),
                authorizer=ws_authorizer,
            ),
            disconnect_route_options=apigwv2.WebSocketRouteOptions(
                integration=apigwv2_int.WebSocketLambdaIntegration(
                    'DisconnectIntegration', self.ws_disconnect_fn),
            ),
            default_route_options=apigwv2.WebSocketRouteOptions(
                integration=apigwv2_int.WebSocketLambdaIntegration(
                    'DefaultIntegration', self.ws_default_fn),
            ),
        )
        # No AccessLogSettings here on purpose: the ID token rides the query string
        # on the handshake, so an access log would capture it. If logging is ever
        # added, use a custom format that never references the token or the raw path.
        self.ws_stage = apigwv2.WebSocketStage(
            self, 'WsStage',
            web_socket_api=self.ws_api,
            stage_name='prod',
            auto_deploy=True,
        )

        # $default replies on the connection it received, so point it at the stage
        # callback URL now that the stage exists, scoped to posting to connections.
        self.ws_default_fn.add_environment('WS_ENDPOINT', self.ws_stage.callback_url)
        self.ws_default_fn.add_to_role_policy(iam.PolicyStatement(
            actions=['execute-api:ManageConnections'],
            resources=[self.format_arn(
                service='execute-api',
                resource=f'{self.ws_api.api_id}/prod/POST/@connections/*',
            )],
        ))

        # ------------- OUTPUTS ------------- #
        CfnOutput(self, 'ApiUrl', value=self.api.url)
        CfnOutput(self, 'WsUrl', value=self.ws_stage.url)