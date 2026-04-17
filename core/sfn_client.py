"""
core/sfn_client.py — fire-and-forget Step Functions execution trigger.
"""
import os
import json
import uuid
import boto3


def trigger_step_function(rca_output: dict, ai_critic: dict) -> str:
    """Start a Step Functions execution with RCA payload. Returns execution ARN."""
    sfn = boto3.client("stepfunctions", region_name=os.getenv("AWS_REGION", "ap-south-1"))
    response = sfn.start_execution(
        stateMachineArn=os.environ["SF_STATE_MACHINE_ARN"],
        name=f"inframind-{rca_output.get('incident_id', uuid.uuid4().hex)[:8]}-{uuid.uuid4().hex[:6]}",
        input=json.dumps({"rca_output": rca_output, "ai_critic": ai_critic}),
    )
    return response["executionArn"]
