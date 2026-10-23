"""Create the 3 NovaMart policy Knowledge Bases on the stack's S3 Vectors indexes."""
import sys, time
sys.path.insert(0, 'src')
import boto3, config

agent = boto3.client('bedrock-agent', region_name=config.AWS_REGION)

EMBED_MODEL_ARN = (f"arn:aws:bedrock:{config.AWS_REGION}::foundation-model/"
                   "amazon.titan-embed-text-v2:0")

KBS = [
    ('RETURNS_KB_ID',  'novamart-returns-kb',  'policies/returns/',  config.RETURNS_VECTOR_INDEX),
    ('SHIPPING_KB_ID', 'novamart-shipping-kb', 'policies/shipping/', config.SHIPPING_VECTOR_INDEX),
    ('WARRANTY_KB_ID', 'novamart-warranty-kb', 'policies/warranty/', config.WARRANTY_VECTOR_INDEX),
]


def wait(get_status, ok, label):
    for _ in range(60):
        status = get_status()
        if status in ok:
            return status
        if 'FAIL' in status:
            raise RuntimeError(f"{label} status {status}")
        time.sleep(5)
    raise TimeoutError(label)


print(f"Account {config.ACCOUNT_ID} | region {config.AWS_REGION}")
results = {}
for env_key, name, prefix, index in KBS:
    # 1. Knowledge base on the existing S3 Vectors index (NOT managed)
    kb = agent.create_knowledge_base(
        name=name,
        roleArn=config.AGENTCORE_ROLE_ARN,
        knowledgeBaseConfiguration={
            'type': 'VECTOR',
            'vectorKnowledgeBaseConfiguration': {
                'embeddingModelArn': EMBED_MODEL_ARN,
                'embeddingModelConfiguration': {
                    'bedrockEmbeddingModelConfiguration': {
                        'dimensions': 1024, 'embeddingDataType': 'FLOAT32'}},
            },
        },
        storageConfiguration={
            'type': 'S3_VECTORS',
            's3VectorsConfiguration': {
                'vectorBucketArn': config.VECTOR_STORE_BUCKET_ARN,
                'indexArn': f"{config.VECTOR_STORE_BUCKET_ARN}/index/{index}",
            },
        },
    )['knowledgeBase']
    kb_id = kb['knowledgeBaseId']
    wait(lambda: agent.get_knowledge_base(knowledgeBaseId=kb_id)['knowledgeBase']['status'],
         {'ACTIVE'}, name)

    # 2. Data source = only this KB's folder in the policy bucket
    ds = agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=f"{name}-docs",
        dataSourceConfiguration={
            'type': 'S3',
            's3Configuration': {
                'bucketArn': f"arn:aws:s3:::{config.POLICY_BUCKET}",
                'inclusionPrefixes': [prefix],
            },
        },
    )['dataSource']
    ds_id = ds['dataSourceId']

    # 3. Sync = read files, chunk, embed with Titan, write vectors to the index
    job = agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)['ingestionJob']
    status = wait(lambda: agent.get_ingestion_job(
                      knowledgeBaseId=kb_id, dataSourceId=ds_id,
                      ingestionJobId=job['ingestionJobId'])['ingestionJob']['status'],
                  {'COMPLETE'}, f"{name} sync")
    stats = agent.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id,
                                    ingestionJobId=job['ingestionJobId'])['ingestionJob']['statistics']
    print(f"{name}: {kb_id}  sync {status}, documents indexed: "
          f"{stats.get('numberOfNewDocumentsIndexed')}")
    results[env_key] = kb_id

print("\nAdd to .env:")
for k, v in results.items():
    print(f"{k}={v}")
