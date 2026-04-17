import boto3, json, os
from datetime import datetime

dynamo = boto3.resource('dynamodb')
table  = dynamo.Table('rca_reviews')

def lambda_handler(event, context):
    rca     = event['rca_output']
    critic  = event.get('ai_critic', {})

    table.put_item(Item={
        'incident_id':   rca['incident_id'],
        'task_token':    event.get('task_token'),
        'rca_output':    json.dumps(rca),
        'raw_log':       rca.get('raw_log', ''),
        'severity':      rca.get('severity', 'Unknown'),
        'log_service':   rca.get('log_service', ''),
        'log_severity':  rca.get('log_severity', ''),
        'log_format':    rca.get('log_format', ''),
        'model_used':    rca.get('model_used', ''),
        'mlflow_run_id': rca.get('mlflow_run_id', ''),
        'confidence':    str(rca.get('confidence', '')),
        'attempts':      rca.get('attempts', 1),
        'ai_critic':     json.dumps(critic),
        'critic_score':  critic.get('score'),
        'status':        'pending',
        'reviewer_id':   None,
        'created_at':    datetime.utcnow().isoformat()
    })

    return {'status': 'stored'}