from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_sqs as sqs,
    aws_events as events,
    aws_events_targets as targets,
    aws_cloudwatch as cloudwatch,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_iam as iam,
    aws_secretsmanager as secretsmanager,
    aws_scheduler as scheduler,
)
from constructs import Construct


class SettlementStack(Stack):
    """Live-results and bet-settlement pipeline.

    The Results Ingest poller (added in a later step) marks a match FINAL and
    emits match.settled onto a custom EventBridge bus. A rule routes that event
    into an SQS queue, and a durable Lambda drains the queue one match at a time,
    settling every PENDING bet on it. A dead-letter queue plus an alarm catch any
    message that fails settlement.

    A separate aggregator consumes the Bets table stream and keeps the realized-
    profit leaderboard, broadcasting it to every live connection as bets settle.
    """

    _EVENT_SOURCE = "worldcup.results"
    _EVENT_DETAIL_TYPE = "match.settled"
    _SETTLEMENT_TIMEOUT = Duration.seconds(60)
    _RUNTIME = _lambda.Runtime.PYTHON_3_14
    _ARCH = _lambda.Architecture.ARM_64
    _FUNCTION_NAME = "worldcup-settlement"
    _NOTIFY_FUNCTION_NAME = "worldcup-notify"
    _NOTIFY_TIMEOUT = Duration.seconds(30)
    _WS_STAGE = "prod"
    _RESULTS_FUNCTION_NAME = "worldcup-results"
    _RESULTS_TIMEOUT = Duration.seconds(60)
    _RESULTS_POLL_RATE = "rate(1 minute)"
    _FEED_SECRET_NAME = "worldcup/football-data-token"
    _FEED_URL = "https://api.football-data.org/v4/competitions/WC/matches"
    _LEADERBOARD_FUNCTION_NAME = "worldcup-leaderboard"
    _LEADERBOARD_TIMEOUT = Duration.seconds(30)

    def __init__(self, scope: Construct, construct_id: str, *,
                 bets_table, wallets_table, matches_table, teams_table,
                 leaderboard_table,
                 ws_api, ws_stage, ws_connections_table,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- transport ------------------------------------------------------
        self.bus = events.EventBus(self, "EventBus", event_bus_name="worldcup-events")

        self.settlement_dlq = sqs.Queue(
            self, "SettlementDlq",
            queue_name="worldcup-match-settlement-dlq",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )

        self.settlement_queue = sqs.Queue(
            self, "SettlementQueue",
            queue_name="worldcup-match-settlement",
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.days(14),
            visibility_timeout=Duration.seconds(self._SETTLEMENT_TIMEOUT.to_seconds() * 6),
            receive_message_wait_time=Duration.seconds(20),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=self.settlement_dlq,
            ),
        )

        self.settlement_rule = events.Rule(
            self, "MatchSettledRule",
            rule_name="worldcup-match-settled",
            event_bus=self.bus,
            event_pattern=events.EventPattern(
                source=[self._EVENT_SOURCE],
                detail_type=[self._EVENT_DETAIL_TYPE],
            ),
        )
        self.settlement_rule.add_target(targets.SqsQueue(self.settlement_queue))

        cloudwatch.Alarm(
            self, "SettlementDlqAlarm",
            alarm_name="worldcup-match-settlement-dlq-not-empty",
            metric=self.settlement_dlq.metric_approximate_number_of_messages_visible(
                period=Duration.minutes(1),
                statistic="Maximum",
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # --- shared domain layer (own instance, not a cross-stack reference) -
        self.domain_layer = _lambda.LayerVersion(
            self, "DomainLayer",
            code=_lambda.Code.from_asset(
                "assets/domain",
                exclude=["**/__pycache__", "**/*.pyc"],
            ),
            compatible_runtimes=[self._RUNTIME],
            compatible_architectures=[self._ARCH],
        )

        # --- notify function ------------------------------------------------
        # Settlement hands this its freshly settled bets; per user it pushes a
        # wallet+results update over the WebSocket. Best-effort by design,
        # invoked asynchronously by settlement.
        notify_logs = logs.LogGroup(
            self, "NotifyLogGroup",
            log_group_name="/aws/lambda/" + self._NOTIFY_FUNCTION_NAME,
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.notify_fn = _lambda.Function(
            self, "NotifyFn",
            function_name=self._NOTIFY_FUNCTION_NAME,
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler="index.handler",
            code=_lambda.Code.from_asset("assets/_lambda/func_notify"),
            layers=[self.domain_layer],
            timeout=self._NOTIFY_TIMEOUT,
            memory_size=256,
            environment={
                "WALLETS_TABLE": wallets_table.table_name,
                "WS_ENDPOINT": ws_stage.callback_url,
                "WS_CONNECTIONS_TABLE": ws_connections_table.table_name,
            },
            log_group=notify_logs,
        )

        # Read the post-settlement balance to report it.
        self.notify_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem"],
            resources=[wallets_table.table_arn],
        ))
        # Find the user's live connections (index) and prune any that have gone.
        self.notify_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[
                ws_connections_table.table_arn,
                ws_connections_table.table_arn + "/index/gsi_user",
            ],
        ))
        self.notify_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:DeleteItem"],
            resources=[ws_connections_table.table_arn],
        ))
        # Push the update to those connections.
        self.notify_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["execute-api:ManageConnections"],
            resources=[self.format_arn(
                service="execute-api",
                resource=ws_api.api_id + "/" + self._WS_STAGE + "/POST/@connections/*",
            )],
        ))

        # --- settlement durable function ------------------------------------
        settlement_logs = logs.LogGroup(
            self, "SettlementLogGroup",
            log_group_name="/aws/lambda/" + self._FUNCTION_NAME,
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.settlement_fn = _lambda.Function(
            self, "SettlementFn",
            function_name=self._FUNCTION_NAME,
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler="index.handler",
            code=_lambda.Code.from_asset("assets/_lambda/func_settlement"),
            layers=[self.domain_layer],
            timeout=self._SETTLEMENT_TIMEOUT,
            memory_size=256,
            durable_config=_lambda.DurableConfig(
                execution_timeout=Duration.minutes(5),
                retention_period=Duration.days(14),
            ),
            environment={
                "BETS_TABLE": bets_table.table_name,
                "WALLETS_TABLE": wallets_table.table_name,
                "NOTIFY_FUNCTION": self.notify_fn.function_arn,
            },
            log_group=settlement_logs,
        )

        # --- IAM (hand-rolled, scoped) --------------------------------------
        # Poll-based source: the function role consumes the queue itself.
        self.settlement_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
            resources=[self.settlement_queue.queue_arn],
        ))
        # Read the match's PENDING bets through the match-status index.
        self.settlement_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[bets_table.table_arn, bets_table.table_arn + "/index/gsi_match_status"],
        ))
        # Flip the bet and credit the wallet (the WON transaction + the LOST flip).
        self.settlement_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem"],
            resources=[bets_table.table_arn, wallets_table.table_arn],
        ))
        # Hand settled bets to Notify (asynchronous invoke from inside a step).
        self.settlement_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[self.notify_fn.function_arn],
        ))

        # --- SQS -> settlement, one match per durable execution -------------
        # A durable function cannot be invoked through an unqualified ($LATEST)
        # ARN, so the event source targets a published version via a stable alias.
        alias = _lambda.Alias(
            self, "SettlementAlias",
            alias_name="live",
            version=self.settlement_fn.current_version,
        )
        _lambda.CfnEventSourceMapping(
            self, "SettlementEsm",
            event_source_arn=self.settlement_queue.queue_arn,
            function_name=alias.function_arn,
            batch_size=1,
        )

        # --- results ingest poller ------------------------------------------
        # The token is a resource here; its value is set out of band (never in
        # source or the template). The poller reads it per run.
        self.feed_secret = secretsmanager.Secret(
            self, "FeedSecret",
            secret_name=self._FEED_SECRET_NAME,
            description="football-data.org API token for the results poller",
            removal_policy=RemovalPolicy.DESTROY,
        )

        results_logs = logs.LogGroup(
            self, "ResultsLogGroup",
            log_group_name="/aws/lambda/" + self._RESULTS_FUNCTION_NAME,
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.results_fn = _lambda.Function(
            self, "ResultsFn",
            function_name=self._RESULTS_FUNCTION_NAME,
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler="index.handler",
            code=_lambda.Code.from_asset("assets/_lambda/func_results"),
            layers=[self.domain_layer],
            timeout=self._RESULTS_TIMEOUT,
            memory_size=256,
            environment={
                "MATCHES_TABLE": matches_table.table_name,
                "EVENT_BUS": self.bus.event_bus_name,
                "FEED_SECRET": self.feed_secret.secret_arn,
                "FEED_URL": self._FEED_URL,
                "TEAMS_TABLE": teams_table.table_name,
                "WS_ENDPOINT": ws_stage.callback_url,
                "WS_CONNECTIONS_TABLE": ws_connections_table.table_name,
            },
            log_group=results_logs,
        )

        # Read the feed token.
        self.results_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue"],
            resources=[self.feed_secret.secret_arn],
        ))
        # Read SCHEDULED and LIVE fixtures through the status index; flip them to
        # LIVE while in play and to FINAL once finished.
        self.results_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:Query"],
            resources=[matches_table.table_arn, matches_table.table_arn + "/index/gsi_status_kickoff"],
        ))
        self.results_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem"],
            resources=[matches_table.table_arn],
        ))
        # Emit match.settled onto the bus the settlement rule already listens on.
        self.results_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["events:PutEvents"],
            resources=[self.bus.event_bus_arn],
        ))
        # Knockout results update both teams' Elo, which the forecaster reads; the resolver
        # also scans the team set to map feed names to our canonical spelling.
        self.results_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Scan"],
            resources=[teams_table.table_arn],
        ))
        # Broadcast running scores and final results to every live connection
        # (scan the registry, prune any that have gone, post to the rest).
        self.results_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:Scan", "dynamodb:DeleteItem"],
            resources=[ws_connections_table.table_arn],
        ))
        self.results_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["execute-api:ManageConnections"],
            resources=[self.format_arn(
                service="execute-api",
                resource=ws_api.api_id + "/" + self._WS_STAGE + "/POST/@connections/*",
            )],
        ))

        # EventBridge Scheduler drives the poll. It assumes a role scoped to
        # invoking just this function.
        results_schedule_role = iam.Role(
            self, "ResultsScheduleRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        results_schedule_role.add_to_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[self.results_fn.function_arn],
        ))
        scheduler.CfnSchedule(
            self, "ResultsSchedule",
            name="worldcup-results-poll",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression=self._RESULTS_POLL_RATE,
            target=scheduler.CfnSchedule.TargetProperty(
                arn=self.results_fn.function_arn,
                role_arn=results_schedule_role.role_arn,
            ),
        )

        # --- leaderboard aggregator -----------------------------------------
        # The Bets table stream drives this. When a bet settles (PENDING -> WON
        # or LOST) it adds the bet's realized profit to the player's tournament
        # standing and broadcasts the refreshed leaderboard to every live
        # connection. A whole match settles as one stream batch, so we apply the
        # batch and broadcast the board once.
        leaderboard_logs = logs.LogGroup(
            self, "LeaderboardLogGroup",
            log_group_name="/aws/lambda/" + self._LEADERBOARD_FUNCTION_NAME,
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.leaderboard_fn = _lambda.Function(
            self, "LeaderboardFn",
            function_name=self._LEADERBOARD_FUNCTION_NAME,
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler="index.handler",
            code=_lambda.Code.from_asset("assets/_lambda/func_leaderboard_agg"),
            layers=[self.domain_layer],
            timeout=self._LEADERBOARD_TIMEOUT,
            memory_size=256,
            environment={
                "LEADERBOARD_TABLE": leaderboard_table.table_name,
                "WS_ENDPOINT": ws_stage.callback_url,
                "WS_CONNECTIONS_TABLE": ws_connections_table.table_name,
            },
            log_group=leaderboard_logs,
        )

        # Read the Bets stream. The three data actions scope to the stream ARN;
        # ListStreams is account-scoped and cannot be narrowed to one stream.
        self.leaderboard_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator"],
            resources=[bets_table.table_stream_arn],
        ))
        self.leaderboard_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:ListStreams"],
            resources=["*"],
        ))
        # Update each player's standing and read the board back to broadcast it.
        self.leaderboard_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:UpdateItem", "dynamodb:Query"],
            resources=[leaderboard_table.table_arn],
        ))
        # Broadcast the refreshed board to every live connection (scan the
        # registry, prune any that have gone, post to the rest).
        self.leaderboard_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:Scan", "dynamodb:DeleteItem"],
            resources=[ws_connections_table.table_arn],
        ))
        self.leaderboard_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["execute-api:ManageConnections"],
            resources=[self.format_arn(
                service="execute-api",
                resource=ws_api.api_id + "/" + self._WS_STAGE + "/POST/@connections/*",
            )],
        ))

        # Bets stream -> aggregator. Not a durable function, so the unqualified
        # function ARN is fine (no published-version alias needed).
        _lambda.CfnEventSourceMapping(
            self, "LeaderboardEsm",
            event_source_arn=bets_table.table_stream_arn,
            function_name=self.leaderboard_fn.function_arn,
            starting_position="LATEST",
            batch_size=100,
        )

        # --- health alarms --------------------------------------------------
        # An Errors alarm per pipeline function: if any of the backbone Lambdas
        # starts failing (bad deploy, AWS fault, permissions), it shows red in
        # CloudWatch. Idle functions stay OK (NOT_BREACHING on missing data), so
        # there are no false alarms when nothing is settling.
        def _error_alarm(construct_id, fn, alarm_name):
            cloudwatch.Alarm(
                self, construct_id,
                alarm_name=alarm_name,
                metric=fn.metric_errors(
                    period=Duration.minutes(5),
                    statistic="Sum",
                ),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )

        _error_alarm("SettlementErrorsAlarm", self.settlement_fn, "worldcup-settlement-errors")
        _error_alarm("ResultsErrorsAlarm", self.results_fn, "worldcup-results-errors")
        _error_alarm("LeaderboardErrorsAlarm", self.leaderboard_fn, "worldcup-leaderboard-errors")
        _error_alarm("NotifyErrorsAlarm", self.notify_fn, "worldcup-notify-errors")

        # --- feed-outage alarm ----------------------------------------------
        # The poller logs "results: feed fetch failed" when it cannot reach the
        # football-data feed. A single blip self-heals next tick, so we turn that
        # log line into a metric and alarm only on a sustained outage (the failure
        # present in 3 consecutive 1-minute polls), which means scores, live/final
        # transitions, and settlement have silently stalled.
        feed_failure_metric = results_logs.add_metric_filter(
            "FeedFetchFailedFilter",
            filter_pattern=logs.FilterPattern.literal('"results: feed fetch failed"'),
            metric_namespace="WorldCup/Results",
            metric_name="FeedFetchFailed",
            metric_value="1",
            default_value=0,
        )

        cloudwatch.Alarm(
            self, "FeedOutageAlarm",
            alarm_name="worldcup-results-feed-outage",
            metric=feed_failure_metric.metric(
                period=Duration.minutes(1),
                statistic="Sum",
            ),
            threshold=1,
            evaluation_periods=3,
            datapoints_to_alarm=3,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # --- settled-score divergence alarm ---------------------------------
        # The poller self-heals the displayed score when the feed corrects an
        # already-FINAL match, logging "results: settled score changed". But the
        # bets were paid on the old score, so that marker means a settlement needs
        # reconciling. Turn it into a metric and alarm on a single occurrence.
        settled_changed_metric = results_logs.add_metric_filter(
            "SettledScoreChangedFilter",
            filter_pattern=logs.FilterPattern.literal('"results: settled score changed"'),
            metric_namespace="WorldCup/Results",
            metric_name="SettledScoreChanged",
            metric_value="1",
            default_value=0,
        )

        cloudwatch.Alarm(
            self, "SettledScoreChangedAlarm",
            alarm_name="worldcup-results-settled-score-changed",
            metric=settled_changed_metric.metric(
                period=Duration.minutes(1),
                statistic="Sum",
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )