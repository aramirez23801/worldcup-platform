from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_lambda as _lambda,
    aws_logs as logs,
    aws_cognito as cognito,
    aws_ssm as ssm,
    aws_iam as iam,
)
from constructs import Construct


class AuthStack(Stack):
    """
    Cognito user pool with email sign-in, a hosted UI, and a public SPA client.
    A PostConfirmation Lambda seeds each new user a starting wallet.
    """
    _RUNTIME = _lambda.Runtime.PYTHON_3_14
    _ARCH = _lambda.Architecture.ARM_64
    _STARTING_BALANCE = '10000'

    def __init__(self, scope: Construct, construct_id: str, *,
                 wallets_table, domain_prefix: str = 'worldcup-auth',
                 callback_urls: list[str] | None = None,
                 logout_urls: list[str] | None = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        callback_urls = callback_urls or ['http://localhost:5173/callback']
        logout_urls = logout_urls or ['http://localhost:5173']

        # ------------- SEED WALLET LAMBDA ------------- #
        lg_seed_wallet = logs.LogGroup(
            self, 'LogSeedWallet',
            log_group_name='/aws/lambda/seed_wallet',
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )
        func_seed_wallet = _lambda.Function(
            self, 'FuncSeedWallet',
            function_name='seed_wallet',
            handler='index.handler',
            code=_lambda.Code.from_asset('assets/_lambda/func_seed_wallet'),
            runtime=self._RUNTIME,
            architecture=self._ARCH,
            timeout=Duration.seconds(10),
            log_group=lg_seed_wallet,
            environment={
                'WALLETS_TABLE': wallets_table.table_name,
                'STARTING_BALANCE': self._STARTING_BALANCE,
            },
        )
        # Least privilege: this function only ever does a conditional PutItem on the wallet.
        func_seed_wallet.add_to_role_policy(iam.PolicyStatement(
            actions=['dynamodb:PutItem'],
            resources=[wallets_table.table_arn],
        ))

        # ------------- USER POOL ------------- #
        user_pool = cognito.UserPool(
            self, 'UserPool',
            user_pool_name='worldcup-users',
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=False,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            lambda_triggers=cognito.UserPoolTriggers(
                post_confirmation=func_seed_wallet,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------- HOSTED UI DOMAIN ------------- #
        # Prefix must be globally unique. The account id keeps it unique without hardcoding.
        user_pool_domain = user_pool.add_domain(
            'HostedUiDomain',
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f'{domain_prefix}-{self.account}',
            ),
        )

        # ------------- APP CLIENT ------------- #
        # Public SPA client (no secret, PKCE). Localhost callback for dev now; the CloudFront URL is added in the frontend phase.
        user_pool_client = user_pool.add_client(
            'SpaClient',
            user_pool_client_name='worldcup-spa',
            generate_secret=False,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=callback_urls,
                logout_urls=logout_urls,
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO,
            ],
            prevent_user_existence_errors=True,
        )

        # Managed login (Essentials tier) renders from a branding resource. On a
        # fresh deploy with no branding attached, the page can come up unstyled;
        # attaching a branding set with Cognito's own default values guarantees
        # the standard managed-login styling applies. We intentionally keep the
        # default look (no custom colors), so this just pins "use the default".
        cognito.CfnManagedLoginBranding(
            self, 'ManagedLoginBranding',
            user_pool_id=user_pool.user_pool_id,
            client_id=user_pool_client.user_pool_client_id,
            use_cognito_provided_values=True,
        )

        hosted_ui_url = user_pool_domain.base_url()

        # ------------- CONFIG FOR FRONTEND AND API ------------- #
        ssm.StringParameter(
            self, 'ParamUserPoolId',
            parameter_name='/worldcup/auth/userPoolId',
            string_value=user_pool.user_pool_id,
        )
        ssm.StringParameter(
            self, 'ParamUserPoolClientId',
            parameter_name='/worldcup/auth/userPoolClientId',
            string_value=user_pool_client.user_pool_client_id,
        )
        ssm.StringParameter(
            self, 'ParamHostedUiDomain',
            parameter_name='/worldcup/auth/hostedUiDomain',
            string_value=hosted_ui_url,
        )

        # Expose for later stacks (the API authorizer needs the pool and client).
        self.user_pool = user_pool
        self.user_pool_client = user_pool_client
        self.hosted_ui_domain = hosted_ui_url

        CfnOutput(self, 'UserPoolId', value=user_pool.user_pool_id)
        CfnOutput(self, 'UserPoolClientId', value=user_pool_client.user_pool_client_id)
        CfnOutput(self, 'HostedUiDomain', value=hosted_ui_url)