import os

from aws_cdk import (
    Stack,
    RemovalPolicy,
    Duration,
    CfnOutput,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_wafv2 as wafv2,
)
from constructs import Construct


class FrontendStack(Stack):
    """
    Static hosting for the React SPA: a private S3 bucket behind CloudFront with
    Origin Access Control and an AWS WAF web ACL. A BucketDeployment uploads the
    built app together with a config.json written at deploy time from the live API,
    WebSocket, and Cognito values. The app fetches /config.json at startup, so the
    same build runs in any account with nothing hardcoded.
    """
    # The built app. `npm run build` in frontend/ produces this before `cdk deploy`.
    _BUILD_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'dist')

    def __init__(self, scope: Construct, construct_id: str, *,
                 api_url: str, ws_url: str,
                 user_pool, user_pool_client, cognito_domain: str,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ------------- ORIGIN BUCKET ------------- #
        # Private; only CloudFront reads it through OAC. Destroyable so the whole
        # stack tears down and redeploys cleanly.
        site_bucket = s3.Bucket(
            self, 'SiteBucket',
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ------------- WAF ------------- #
        # CloudFront-scoped web ACL (must live in us-east-1, which this stack is).
        # Default allow with the AWS common rule set in front: minimal but real.
        web_acl = wafv2.CfnWebACL(
            self, 'WebAcl',
            scope='CLOUDFRONT',
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name='worldcup-frontend',
                sampled_requests_enabled=True,
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name='CommonRuleSet',
                    priority=1,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name='AWS',
                            name='AWSManagedRulesCommonRuleSet',
                        ),
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name='worldcup-frontend-common',
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )

        # ------------- CLOUDFRONT ------------- #
        # OAC origin (the modern replacement for OAI): CloudFront signs requests to
        # the private bucket and the construct writes the matching bucket policy.
        # SPA routing: under OAC, S3 answers unknown keys with 403, so map 403 and
        # 404 to index.html with 200 so client-side routes resolve.
        distribution = cloudfront.Distribution(
            self, 'Distribution',
            default_root_object='index.html',
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            web_acl_id=web_acl.attr_arn,
            comment='World Cup platform SPA',
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path='/index.html',
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path='/index.html',
                    ttl=Duration.seconds(0),
                ),
            ],
        )

        # ------------- DEPLOY: BUILT APP + RUNTIME CONFIG ------------- #
        # Two sources land in the bucket root: the built static app, and a
        # config.json written at deploy time from the live cross-stack values, so
        # the bundle itself carries no environment. The REST url keeps API
        # Gateway's trailing slash; the app strips it when reading config.
        config = {
            'apiUrl': api_url,
            'wsUrl': ws_url,
            'cognitoAuthority': f'https://cognito-idp.{self.region}.amazonaws.com/{user_pool.user_pool_id}',
            'cognitoClientId': user_pool_client.user_pool_client_id,
            'cognitoDomain': cognito_domain,
            'region': self.region,
        }
        # The built app, if present. Until `npm run build` has run in frontend/,
        # fall back to a placeholder so the app still synthesizes; the real upload
        # happens once the frontend is built and this stack is deployed.
        if os.path.isdir(self._BUILD_DIR):
            app_source = s3deploy.Source.asset(self._BUILD_DIR)
        else:
            app_source = s3deploy.Source.data(
                'index.html',
                '<!doctype html><meta charset="utf-8"><title>World Cup</title>',
            )

        s3deploy.BucketDeployment(
            self, 'DeploySite',
            destination_bucket=site_bucket,
            distribution=distribution,
            distribution_paths=['/*'],
            memory_limit=256,
            sources=[
                app_source,
                s3deploy.Source.json_data('config.json', config),
            ],
        )

        # ------------- OUTPUTS ------------- #
        site_url = f'https://{distribution.distribution_domain_name}'
        CfnOutput(self, 'SiteUrl', value=site_url)
        CfnOutput(self, 'DistributionId', value=distribution.distribution_id)
        CfnOutput(self, 'SiteBucketName', value=site_bucket.bucket_name)

        # Exposed in case a later stack needs the distribution or url.
        self.distribution = distribution
        self.site_url = site_url