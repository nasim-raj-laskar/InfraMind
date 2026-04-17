from __future__ import annotations
import logging

logger = logging.getLogger("inframind.dag")


def task_review_sent(**context):
    from core.sfn_client import trigger_step_function
    from config.config import S3_BUCKET
    from dags.ingestion import move_to_processed

    results = context["ti"].xcom_pull(key="rca_results", task_ids="run_rca") or []
    s3_keys = context["ti"].xcom_pull(key="s3_keys",     task_ids="fetch_logs") or []

    for result in results:
        if "rca_output" not in result:
            logger.warning("Skipping failed RCA entry: %s", result.get("error"))
            continue
        arn = trigger_step_function(
            rca_output=result["rca_output"],
            ai_critic=result["ai_critic"],
        )
        logger.info("SF execution started | arn=%s", arn)

    if s3_keys:
        move_to_processed(bucket=S3_BUCKET, keys=s3_keys)
        logger.info("Moved %d logs to processed/", len(s3_keys))

    logger.info("Triggered %d Step Functions execution(s)",
                sum(1 for r in results if "rca_output" in r))
