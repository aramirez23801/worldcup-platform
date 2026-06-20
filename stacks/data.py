import json
import os
 
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    CustomResource,
    aws_dynamodb as ddb,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
    custom_resources as cr,
)
from constructs import Construct
 
 
class DataStack(Stack):
    """
    DynamoDB tables for the platform. Common settings live in class constants so
    every table is consistent: on-demand billing, AWS-managed KMS encryption, and
    DESTROY removal for clean teardown.
    """
    _BILLING = ddb.BillingMode.PAY_PER_REQUEST
    _ENCRYPTION = ddb.TableEncryption.AWS_MANAGED
    _REMOVAL = RemovalPolicy.DESTROY
    _RUNTIME = _lambda.Runtime.PYTHON_3_14
    _ARCH = _lambda.Architecture.ARM_64
 
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
 
        # ------------- WALLETS ------------- #
        self.wallets = ddb.Table(
            self, 'WalletsTable',
            table_name='Wallets',
            partition_key=ddb.Attribute(name='userId', type=ddb.AttributeType.STRING),
            billing_mode=self._BILLING,
            encryption=self._ENCRYPTION,
            removal_policy=self._REMOVAL,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )
 
        # ------------- MATCHES ------------- #
        self.matches = ddb.Table(
            self, 'MatchesTable',
            table_name='Matches',
            partition_key=ddb.Attribute(name='matchId', type=ddb.AttributeType.STRING),
            billing_mode=self._BILLING,
            encryption=self._ENCRYPTION,
            removal_policy=self._REMOVAL,
        )
        # Query upcoming matches by status, ordered by kickoff.
        self.matches.add_global_secondary_index(
            index_name='gsi_status_kickoff',
            partition_key=ddb.Attribute(name='status', type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name='kickoff', type=ddb.AttributeType.STRING),
            projection_type=ddb.ProjectionType.ALL,
        )
 
        # ------------- MATCHES SCHEDULE SEED ------------- #
        # The fixed tournament structure (groups with real teams, knockout
        # matches with their bracket references) is built from the committed
        # fixtures by scripts/build_schedule.py and written to
        # assets/seed/schedule.json. This custom resource reads that file at synth
        # and writes the 104 matches into the Matches table on deploy, and again
        # whenever the schedule changes. Live data, scores, status changes, and
        # resolved knockout teams, is filled later by the results poller.
        schedule_path = os.path.join(
            os.path.dirname(__file__), '..', 'assets', 'seed', 'schedule.json'
        )
        with open(schedule_path, encoding='utf-8') as f:
            schedule_seed = json.load(f)
 
        self.matches_seed_logs = logs.LogGroup(
            self, 'MatchesSeedLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=self._REMOVAL,
        )
        self.matches_seed_fn = _lambda.Function(
            self, 'MatchesSeedFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_seed_matches')
            ),
            timeout=Duration.seconds(60),
            memory_size=256,
            log_group=self.matches_seed_logs,
            environment={'MATCHES_TABLE': self.matches.table_name},
        )
        # Least privilege: batch_writer needs only BatchWriteItem, scoped to Matches.
        self.matches_seed_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=['dynamodb:BatchWriteItem'],
                resources=[self.matches.table_arn],
            )
        )
        self.matches_seed_provider = cr.Provider(
            self, 'MatchesSeedProvider',
            on_event_handler=self.matches_seed_fn,
        )
        CustomResource(
            self, 'MatchesSeed',
            service_token=self.matches_seed_provider.service_token,
            properties={'matches': schedule_seed['matches']},
        )
 
        # ------------- BETS ------------- #
        self.bets = ddb.Table(
            self, 'BetsTable',
            table_name='Bets',
            partition_key=ddb.Attribute(name='userId', type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name='betId', type=ddb.AttributeType.STRING),
            billing_mode=self._BILLING,
            encryption=self._ENCRYPTION,
            removal_policy=self._REMOVAL,
            stream=ddb.StreamViewType.NEW_AND_OLD_IMAGES,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
        )
        # Load all bets for a match by status, for settlement fan-out.
        self.bets.add_global_secondary_index(
            index_name='gsi_match_status',
            partition_key=ddb.Attribute(name='matchId', type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name='status', type=ddb.AttributeType.STRING),
            projection_type=ddb.ProjectionType.ALL,
        )
 
        # ------------- TEAMS ------------- #
        self.teams = ddb.Table(
            self, 'TeamsTable',
            table_name='Teams',
            partition_key=ddb.Attribute(name='teamId', type=ddb.AttributeType.STRING),
            billing_mode=self._BILLING,
            encryption=self._ENCRYPTION,
            removal_policy=self._REMOVAL,
        )
 
        # ------------- TEAMS ELO SEED ------------- #
        # Baseline Elo ratings are computed from the committed match history by
        # scripts/build_elo.py and written to assets/seed/elo_ratings.json. This
        # custom resource reads that file at synth and writes the ratings into the
        # Teams table on deploy, and again whenever the ratings change. The live
        # per-match Elo updates during the tournament are a separate function.
        ratings_path = os.path.join(
            os.path.dirname(__file__), '..', 'assets', 'seed', 'elo_ratings.json'
        )
        with open(ratings_path, encoding='utf-8') as f:
            elo_seed = json.load(f)
 
        self.elo_seed_logs = logs.LogGroup(
            self, 'EloSeedLogs',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=self._REMOVAL,
        )
        self.elo_seed_fn = _lambda.Function(
            self, 'EloSeedFn',
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            handler='index.handler',
            code=_lambda.Code.from_asset(
                os.path.join(os.path.dirname(__file__), '..', 'assets', '_lambda', 'func_seed_elo')
            ),
            timeout=Duration.seconds(60),
            memory_size=256,
            log_group=self.elo_seed_logs,
            environment={'TEAMS_TABLE': self.teams.table_name},
        )
        # Least privilege: batch_writer needs only BatchWriteItem, scoped to Teams.
        self.elo_seed_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=['dynamodb:BatchWriteItem'],
                resources=[self.teams.table_arn],
            )
        )
        self.elo_seed_provider = cr.Provider(
            self, 'EloSeedProvider',
            on_event_handler=self.elo_seed_fn,
        )
        CustomResource(
            self, 'EloSeed',
            service_token=self.elo_seed_provider.service_token,
            properties={'teams': elo_seed['teams']},
        )
 
        # ------------- NOTIFICATIONS ------------- #
        self.notifications = ddb.Table(
            self, 'NotificationsTable',
            table_name='Notifications',
            partition_key=ddb.Attribute(name='userId', type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name='ts', type=ddb.AttributeType.STRING),
            billing_mode=self._BILLING,
            encryption=self._ENCRYPTION,
            removal_policy=self._REMOVAL,
            time_to_live_attribute='expiresAt',
        )
 
        # ------------- LEADERBOARD ------------- #
        # pk holds the scope: "LB#TOURNAMENT" or "LB#MATCH#<matchId>".
        self.leaderboard = ddb.Table(
            self, 'LeaderboardTable',
            table_name='Leaderboard',
            partition_key=ddb.Attribute(name='pk', type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name='userId', type=ddb.AttributeType.STRING),
            billing_mode=self._BILLING,
            encryption=self._ENCRYPTION,
            removal_policy=self._REMOVAL,
        )
 
        # ------------- WEBSOCKET CONNECTIONS ------------- #
        self.ws_connections = ddb.Table(
            self, 'WsConnectionsTable',
            table_name='WsConnections',
            partition_key=ddb.Attribute(name='connectionId', type=ddb.AttributeType.STRING),
            billing_mode=self._BILLING,
            encryption=self._ENCRYPTION,
            removal_policy=self._REMOVAL,
            time_to_live_attribute='expiresAt',
        )
        # Look up a user's live connections to push updates.
        self.ws_connections.add_global_secondary_index(
            index_name='gsi_user',
            partition_key=ddb.Attribute(name='userId', type=ddb.AttributeType.STRING),
            projection_type=ddb.ProjectionType.KEYS_ONLY,
        )
 
        # ------------- OUTPUTS ------------- #
        CfnOutput(self, 'WalletsTableName', value=self.wallets.table_name)
        CfnOutput(self, 'MatchesTableName', value=self.matches.table_name)
        CfnOutput(self, 'BetsTableName', value=self.bets.table_name)
        CfnOutput(self, 'TeamsTableName', value=self.teams.table_name)
        CfnOutput(self, 'NotificationsTableName', value=self.notifications.table_name)
        CfnOutput(self, 'LeaderboardTableName', value=self.leaderboard.table_name)
        CfnOutput(self, 'WsConnectionsTableName', value=self.ws_connections.table_name)