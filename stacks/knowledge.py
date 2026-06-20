import hashlib
import os

from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_s3vectors as s3vectors,
    aws_bedrock as bedrock,
    aws_iam as iam,
    custom_resources as cr,
)
from constructs import Construct


def _corpus_dir_hash(path):
    """Stable hash of the corpus directory contents. Used to force re-ingestion when the corpus changes: the hash is passed to StartIngestionJob so a content change produces a new custom-resource property and triggers on_update."""
    h = hashlib.sha256()
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            h.update(name.encode('utf-8'))
            with open(os.path.join(root, name), 'rb') as fh:
                h.update(fh.read())
    return h.hexdigest()[:16]


class KnowledgeStack(Stack):
    """
    Bedrock Knowledge Base over the World Cup corpus, stored in S3 Vectors and embedded with Cohere Embed English v3. Also defines the Bedrock Guardrail used by the agent.
    """
    _EMBED_MODEL = 'cohere.embed-english-v3'
    _EMBED_DIMS = 1024  # Cohere Embed English v3 output dimension
    _CHUNK_MAX_TOKENS = 512   # sized to hold one head-to-head section per chunk
    _CHUNK_OVERLAP_PCT = 20   # sliding-window overlap for context continuity

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        embed_model_arn = f'arn:aws:bedrock:{self.region}::foundation-model/{self._EMBED_MODEL}'
        corpus_hash = _corpus_dir_hash('assets/kb_corpus')

        # ------------- DOCS BUCKET (source markdown) ------------- #
        # SSE-S3 (AES256). The corpus is public football facts, so SSE-S3 encrypts at rest without forcing readers to carry broad kms permissions for the AWS managed S3 key.
        docs_bucket = s3.Bucket(
            self, 'DocsBucket',
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        deploy_corpus = s3deploy.BucketDeployment(
            self, 'DeployCorpus',
            sources=[s3deploy.Source.asset('assets/kb_corpus')],
            destination_bucket=docs_bucket,
            memory_limit=1024,
        )

        # ------------- S3 VECTORS STORE ------------- #
        vector_bucket = s3vectors.CfnVectorBucket(
            self, 'VectorBucket',
            vector_bucket_name='worldcup-vectors',
        )
        vector_index = s3vectors.CfnIndex(
            self, 'VectorIndex',
            index_name='worldcup-kb-index',
            vector_bucket_name=vector_bucket.vector_bucket_name,
            dimension=self._EMBED_DIMS,
            distance_metric='cosine',
            data_type='float32',
            metadata_configuration=s3vectors.CfnIndex.MetadataConfigurationProperty(
                non_filterable_metadata_keys=['AMAZON_BEDROCK_TEXT', 'AMAZON_BEDROCK_METADATA'],
            ),
        )
        vector_index.add_dependency(vector_bucket)

        # ------------- KB SERVICE ROLE ------------- #
        kb_role = iam.Role(
            self, 'KbRole',
            assumed_by=iam.ServicePrincipal(
                'bedrock.amazonaws.com',
                conditions={'StringEquals': {'aws:SourceAccount': self.account}},
            ),
        )
        # Read-only on the corpus bucket: GetObject for the markdown, ListBucket to enumerate it. Hand-rolled to match the scoped-statement convention used across these stacks (no grant_read helper); SSE-S3 needs no KMS grant.
        kb_role.add_to_policy(iam.PolicyStatement(
            actions=['s3:GetObject', 's3:ListBucket'],
            resources=[docs_bucket.bucket_arn, docs_bucket.arn_for_objects('*')],
        ))
        kb_role.add_to_policy(iam.PolicyStatement(
            actions=['bedrock:InvokeModel'],
            resources=[embed_model_arn],
        ))
        kb_role.add_to_policy(iam.PolicyStatement(
            actions=[
                's3vectors:GetIndex',
                's3vectors:QueryVectors',
                's3vectors:PutVectors',
                's3vectors:GetVectors',
                's3vectors:ListVectors',
                's3vectors:DeleteVectors',
            ],
            resources=[vector_index.attr_index_arn],
        ))
        kb_role.add_to_policy(iam.PolicyStatement(
            actions=['s3vectors:GetVectorBucket'],
            resources=[vector_bucket.attr_vector_bucket_arn],
        ))

        # ------------- KNOWLEDGE BASE ------------- #
        kb = bedrock.CfnKnowledgeBase(
            self, 'KnowledgeBase',
            name='worldcup-knowledge',
            role_arn=kb_role.role_arn,
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type='VECTOR',
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=embed_model_arn,
                ),
            ),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type='S3_VECTORS',
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    vector_bucket_arn=vector_bucket.attr_vector_bucket_arn,
                    index_arn=vector_index.attr_index_arn,
                ),
            ),
        )
        kb.add_dependency(vector_index)
        kb.node.add_dependency(kb_role)

        # ------------- DATA SOURCE ------------- #
        data_source = bedrock.CfnDataSource(
            self, 'DocsDataSource',
            knowledge_base_id=kb.attr_knowledge_base_id,
            name='worldcup-docs',
            data_deletion_policy='DELETE',
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type='S3',
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=docs_bucket.bucket_arn,
                ),
            ),
            # Fixed-size chunking sized to our document sections. On S3 Vectors, hierarchical is discouraged and semantic has a per-file size cliff, so fixed-size at 512 tokens is the reliable, production-recommended choice.
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(
                    chunking_strategy='FIXED_SIZE',
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=self._CHUNK_MAX_TOKENS,
                        overlap_percentage=self._CHUNK_OVERLAP_PCT,
                    ),
                ),
            ),
        )

        # ------------- INGESTION (on deploy and on corpus change) ------------- #
        # The corpus hash rides along in the job description so that a regenerated corpus changes the custom-resource properties and triggers on_update, re-ingesting.
        ingest_call = cr.AwsSdkCall(
            service='bedrock-agent',
            action='StartIngestionJob',
            parameters={
                'knowledgeBaseId': kb.attr_knowledge_base_id,
                'dataSourceId': data_source.attr_data_source_id,
                'description': f'corpus {corpus_hash}',
            },
            physical_resource_id=cr.PhysicalResourceId.of('worldcup-ingestion'),
        )
        ingest = cr.AwsCustomResource(
            self, 'StartIngestion',
            on_create=ingest_call,
            on_update=ingest_call,
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=['bedrock:StartIngestionJob'],
                    resources=[kb.attr_knowledge_base_arn],
                ),
            ]),
            install_latest_aws_sdk=False,
        )
        ingest.node.add_dependency(data_source)
        ingest.node.add_dependency(deploy_corpus)

        # ------------- GUARDRAIL ------------- #
        guardrail = bedrock.CfnGuardrail(
            self, 'Guardrail',
            name='worldcup-guardrail',
            blocked_input_messaging="I can only help with World Cup facts and this fake-money game.",
            blocked_outputs_messaging="I can only help with World Cup facts and this fake-money game.",
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name='RealFinancialAdvice',
                        definition=(
                            'Real-world financial or investment advice, such as buying or trading stocks, shares, '
                            'crypto, bonds, or ETFs, retirement or brokerage accounts, or how to invest or grow '
                            'personal savings.'
                        ),
                        type='DENY',
                        examples=[
                            'Should I invest my savings in crypto?',
                            'Give me stock investment tips.',
                            'What stocks should I buy right now?',
                            'Is bitcoin a good investment?',
                            'How should I grow my retirement savings?',
                        ],
                    ),
                ],
            ),
        )

        # ------------- EXPOSE FOR THE AGENT STACK ------------- #
        self.knowledge_base_id = kb.attr_knowledge_base_id
        self.knowledge_base_arn = kb.attr_knowledge_base_arn
        self.guardrail_id = guardrail.attr_guardrail_id
        self.guardrail_arn = guardrail.attr_guardrail_arn
        self.guardrail_version = guardrail.attr_version

        # ------------- OUTPUTS ------------- #
        CfnOutput(self, 'KnowledgeBaseId', value=kb.attr_knowledge_base_id)
        CfnOutput(self, 'DataSourceId', value=data_source.attr_data_source_id)
        CfnOutput(self, 'DocsBucketName', value=docs_bucket.bucket_name)
        CfnOutput(self, 'GuardrailId', value=guardrail.attr_guardrail_id)
        CfnOutput(self, 'GuardrailVersion', value=guardrail.attr_version)
