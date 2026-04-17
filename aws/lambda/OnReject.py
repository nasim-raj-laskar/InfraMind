import boto3, json, os
from datetime import datetime

s3     = boto3.client('s3')
dynamo = boto3.resource('dynamodb')
table  = dynamo.Table('rca_reviews')

BUCKET = os.environ['INFRAMIND_S3_BUCKET']

def lambda_handler(event, context):

    # Step Function payload handling
    payload = event.get('rejection', event)

    if 'Cause' in payload:
        payload = json.loads(payload['Cause'])

    incident_id    = payload['incident_id']
    human_feedback = payload['human_feedback']

    print("INCIDENT:", incident_id)

    # fetch record
    record = table.get_item(Key={'incident_id': incident_id})['Item']
    rca    = json.loads(record['rca_output'])

    # use raw_log stored directly in DynamoDB (no S3 read needed)
    original_log = record.get('raw_log', '')

    print("Raw log retrieved from DynamoDB")

    # BUILD NEW LOG CONTENT (log + RCA + feedback)
    new_log = f"""{original_log}

        # === RCA OUTPUT ===
        # summary: {rca.get('summary')}
        # root_cause: {rca.get('root_cause')}
        # fix: {rca.get('immediate_fix')}

        # === HUMAN FEEDBACK ===
        # feedback_type: {human_feedback.get('feedback_type')}
        # reason: {human_feedback.get('reason')}
        # corrected_root_cause: {human_feedback.get('corrected_root_cause')}
        # timestamp: {datetime.utcnow().isoformat()}
        """

    # generate new file name with timestamp
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    new_key = f"raw/rejected_{timestamp}.log"

    print("WRITING NEW FILE:", new_key)

    # write rejected log to S3
    s3.put_object(
        Bucket=BUCKET,
        Key=new_key,
        Body=new_log
    )

    print("New rejected log created")

    # update DynamoDB status
    table.update_item(
        Key={'incident_id': incident_id},
        UpdateExpression='SET #s = :s',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'rejected'}
    )

    print("Dynamo updated")

    return {
        'status': 'rejected',
        'new_log': new_key
    }