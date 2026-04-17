from __future__ import annotations
import time
import logging

from airflow.models import Variable  # type: ignore

logger = logging.getLogger("inframind.dag")


def task_fetch_logs(**context) -> list[str]:
    from dags.ingestion import fetch_logs_from_s3
    from core.metrics import logs_ingested_total, logs_fetch_errors_total

    bucket   = Variable.get("INFRAMIND_S3_BUCKET", default_var="inframind-data-hub")
    prefix   = Variable.get("INFRAMIND_S3_PREFIX",  default_var="raw/")
    max_logs = int(Variable.get("INFRAMIND_MAX_LOGS", default_var="3"))

    try:
        logs, keys = fetch_logs_from_s3(bucket=bucket, prefix=prefix, max_logs=max_logs)
        logs_ingested_total.inc(len(logs))
    except Exception:
        logs_fetch_errors_total.inc()
        raise

    logger.info("Fetched %d logs from S3", len(logs))
    context["ti"].xcom_push(key="raw_logs",  value=logs)
    context["ti"].xcom_push(key="s3_keys",   value=keys)
    context["ti"].xcom_push(key="dag_start", value=time.time())
    return logs
