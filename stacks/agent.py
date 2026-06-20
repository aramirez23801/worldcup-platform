import os
 
from aws_cdk import (
    Stack,
    CfnOutput,
    aws_iam as iam,
    aws_ecr_assets as ecr_assets,
    aws_bedrockagentcore as bac,
)
from constructs import Construct
 
 
class AgentStack(Stack):
    """
    The World Cup agent: a Strands graph (triage plus the Knowledge and Forecast
    specialists) packaged as a container and run as a single Amazon Bedrock
    AgentCore runtime. The image is built from assets/agent and pushed to ECR;
    the runtime serves the agent on POST /invocations and GET /ping. The
    execution role is hand-rolled: the platform permissions AgentCore needs to
    run the container, plus scoped access to the knowledge base, the guardrail,
    and the Teams table.
    """
    _RUNTIME_NAME = 'worldcup_agent'
    #Uncomment if want to rollback to haiku - must "cdk deploy WorldCupAgent"
    #_MODEL_ID = 'us.anthropic.claude-haiku-4-5-20251001-v1:0'
    _MODEL_ID = 'us.anthropic.claude-sonnet-4-6'
    _ARCH = ecr_assets.Platform.LINUX_ARM64
 
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        teams_table,
        bets_table,
        wallets_table,
        matches_table,
        knowledge_base_id,
        knowledge_base_arn,
        guardrail_id,
        guardrail_arn,
        guardrail_version,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
 
        # The model is invoked through a cross-region inference profile. Invoking
        # via a profile needs permission on both the profile ARN and the
        # underlying foundation model in any region the profile may route to. The
        # profile id is "<region prefix>.<model id>"; strip the prefix to get the
        # foundation-model id. Both ARNs are derived from _MODEL_ID (one source of
        # truth) and reused below for IAM and the runtime env var.
        foundation_model_id = self._MODEL_ID.split('.', 1)[1]
        model_arn = f'arn:aws:bedrock:*::foundation-model/{foundation_model_id}'
        profile_arn = f'arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{self._MODEL_ID}'
 
        # ------------- CONTAINER IMAGE ------------- #
        # Build context is assets/ (not assets/agent/) so the image can include
        # the shared domain package alongside the agent app. The .dockerignore at
        # the context root keeps the context to just agent/ and domain/.
        image = ecr_assets.DockerImageAsset(
            self, 'AgentImage',
            directory=os.path.join(os.path.dirname(__file__), '..', 'assets'),
            file=os.path.join('agent', 'Dockerfile'),
            platform=self._ARCH,
        )
 
        # ------------- SHORT-TERM MEMORY ------------- #
        # AgentCore Memory holds the conversation turns per session so the agent
        # can resolve follow-ups. Short-term only (no long-term strategies), so
        # no memory execution role is needed. event_expiry_duration is in days.
        memory = bac.CfnMemory(
            self, 'AgentMemory',
            name='worldcup_agent_memory',
            event_expiry_duration=30,
        )
 
        # ------------- EXECUTION ROLE ------------- #
        # AgentCore Runtime assumes this role. Trust is scoped to this account
        # and to this agent's runtime ARNs (a static name pattern, so no
        # role-to-runtime reference and no dependency cycle) to avoid the
        # confused-deputy problem.
        role = iam.Role(
            self, 'AgentRuntimeRole',
            assumed_by=iam.ServicePrincipal(
                'bedrock-agentcore.amazonaws.com',
                conditions={
                    'StringEquals': {'aws:SourceAccount': self.account},
                    'ArnLike': {
                        'aws:SourceArn': f'arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/{self._RUNTIME_NAME}-*'
                    },
                },
            ),
        )
 
        # Platform: pull the image from the CDK assets repository.
        role.add_to_policy(iam.PolicyStatement(
            actions=['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer'],
            resources=[image.repository.repository_arn],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=['ecr:GetAuthorizationToken'],
            resources=['*'],
        ))
        # Platform: runtime log groups and streams.
        role.add_to_policy(iam.PolicyStatement(
            actions=['logs:CreateLogGroup', 'logs:DescribeLogStreams'],
            resources=[f'arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*'],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=['logs:DescribeLogGroups'],
            resources=[f'arn:aws:logs:{self.region}:{self.account}:log-group:*'],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=['logs:CreateLogStream', 'logs:PutLogEvents'],
            resources=[f'arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*'],
        ))
        # Platform: tracing and namespaced metrics.
        role.add_to_policy(iam.PolicyStatement(
            actions=['xray:PutTraceSegments', 'xray:PutTelemetryRecords',
                     'xray:GetSamplingRules', 'xray:GetSamplingTargets'],
            resources=['*'],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=['cloudwatch:PutMetricData'],
            resources=['*'],
            conditions={'StringEquals': {'cloudwatch:namespace': 'bedrock-agentcore'}},
        ))
        # Platform: model invocation. Scoped to the one model this agent uses:
        # the inference profile and the foundation model it routes to.
        role.add_to_policy(iam.PolicyStatement(
            actions=['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
            resources=[model_arn, profile_arn],
        ))
 
        # App: grounded generation over the knowledge base. RetrieveAndGenerate
        # performs the retrieval internally, so the caller also needs bedrock:Retrieve
        # on the knowledge base, not only bedrock:RetrieveAndGenerate.
        role.add_to_policy(iam.PolicyStatement(
            actions=['bedrock:Retrieve', 'bedrock:RetrieveAndGenerate'],
            resources=[knowledge_base_arn],
        ))
        # App: RetrieveAndGenerate expands the inference profile it uses as the
        # generation model by calling GetInferenceProfile before generating.
        # Converse (used by triage and forecast) does not, so only this path
        # needs it.
        role.add_to_policy(iam.PolicyStatement(
            actions=['bedrock:GetInferenceProfile'],
            resources=[profile_arn],
        ))
        # App: screen the user's incoming message with the guardrail, run as an
        # explicit ApplyGuardrail input check (not attached to any model).
        role.add_to_policy(iam.PolicyStatement(
            actions=['bedrock:ApplyGuardrail'],
            resources=[guardrail_arn],
        ))
        # App: read team Elo ratings for the forecaster.
        role.add_to_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem'],
            resources=[teams_table.table_arn],
        ))
        # App: place fake-coin bets (atomic debit + bet write) and read balances.
        role.add_to_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem', 'dynamodb:UpdateItem'],
            resources=[wallets_table.table_arn],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=['dynamodb:PutItem'],
            resources=[bets_table.table_arn],
        ))
        # App: resolve a team pair to its open fixture (status-index query) and read it.
        role.add_to_policy(iam.PolicyStatement(
            actions=['dynamodb:GetItem', 'dynamodb:Query'],
            resources=[matches_table.table_arn, matches_table.table_arn + '/index/gsi_status_kickoff'],
        ))
        # App: short-term conversation memory (store turns, read recent turns).
        role.add_to_policy(iam.PolicyStatement(
            actions=['bedrock-agentcore:CreateEvent', 'bedrock-agentcore:ListEvents',
                     'bedrock-agentcore:GetEvent'],
            resources=[memory.attr_memory_arn, f'{memory.attr_memory_arn}/*'],
        ))
 
        # ------------- RUNTIME ------------- #
        runtime = bac.CfnRuntime(
            self, 'AgentRuntime',
            agent_runtime_name=self._RUNTIME_NAME,
            agent_runtime_artifact=bac.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=bac.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image.image_uri,
                ),
            ),
            network_configuration=bac.CfnRuntime.NetworkConfigurationProperty(network_mode='PUBLIC'),
            protocol_configuration='HTTP',
            role_arn=role.role_arn,
            environment_variables={
                'MODEL_ID': self._MODEL_ID,
                'TEAMS_TABLE': teams_table.table_name,
                'BETS_TABLE': bets_table.table_name,
                'WALLETS_TABLE': wallets_table.table_name,
                'MATCHES_TABLE': matches_table.table_name,
                'KNOWLEDGE_BASE_ID': knowledge_base_id,
                'KB_MODEL_ARN': profile_arn,
                'GUARDRAIL_ID': guardrail_id,
                'GUARDRAIL_VERSION': guardrail_version,
                'BEDROCK_AGENTCORE_MEMORY_ID': memory.attr_memory_id,
            },
        )
 
        # AgentCore assumes this role and validates the image at create time,
        # so the role's permissions must be attached before the runtime exists.
        # Depending on the role pulls in its default policy as a dependency.
        runtime.node.add_dependency(role)
 
        # ------------- OUTPUTS ------------- #
        # Expose for the REST API (the agent invoker Lambda targets this runtime).
        self.runtime_arn = runtime.attr_agent_runtime_arn
 
        CfnOutput(self, 'AgentRuntimeArn', value=runtime.attr_agent_runtime_arn)
        CfnOutput(self, 'AgentRuntimeName', value=self._RUNTIME_NAME)