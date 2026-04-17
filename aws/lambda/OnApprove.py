import boto3, json, os

s3     = boto3.client('s3')
dynamo = boto3.resource('dynamodb')
table  = dynamo.Table('rca_reviews')

BUCKET = os.environ['INFRAMIND_S3_BUCKET']

def lambda_handler(event, context):
    incident_id = event['incident_id']
    rater_id    = event['rater_id']

    # get record from dynamo
    record = table.get_item(Key={'incident_id': incident_id})['Item']
    rca    = json.loads(record['rca_output'])

    # write rca-results/
    result_key = f"rca-results/results_{incident_id}.json"
    rca['status']      = 'approved'
    rca['approved_by'] = rater_id

    s3.put_object(Bucket=BUCKET, Key=result_key, Body=json.dumps(rca))

    # mark dynamo item done
    table.update_item(
        Key={'incident_id': incident_id},
        UpdateExpression='SET #s = :s, reviewer_id = :r',
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'approved', ':r': rater_id}
    )

    return {'status': 'approved'}