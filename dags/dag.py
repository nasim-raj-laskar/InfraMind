"""
pipeline/dag.py — Airflow DAG for InfraMind RCA pipeline.

Tasks (in order):
  1. fetch_logs      — pull raw logs from S3
  2. normalize_logs  — parse and normalize each log
  3. embed_runbooks  — rebuild ChromaDB if runbooks changed (idempotent)
  4. run_rca         — run autonomous workflow on each log
  5. post_results    — push results to downstream (S3, Slack, webhook)

Each task is independent — if one fails, Airflow retries just that task.
XCom is used to pass data between tasks.
"""
from __future__ import annotations

import json
import time
import logging
from datetime import datetime, timedelta

from airflow import DAG                                                       #type:ignore
from airflow.operators.python import PythonOperator                           #type:ignore
from airflow.models import Variable                                           #type:ignore

logger = logging.getLogger("inframind.dag")

# Default DAG args 
default_args = {
    "owner":            "inframind",
    "depends_on_past":  False,
    "email_on_failure": False,
    "email_on_retry":   False,
    "retries":          2,
    "retry_delay":      timedelta(minutes=5),
}


# TASK FUNCTIONS


def task_fetch_logs(**context) -> list[str]:
    """
    Task 1 — Fetch raw logs from S3.
    Scans the entire raw/ prefix — no date partitioning.
    """
    from dags.ingestion import fetch_logs_from_s3
    from core.metrics import logs_ingested_total, logs_fetch_errors_total

    bucket   = Variable.get("INFRAMIND_S3_BUCKET", default_var="inframind-data-hub")
    prefix   = Variable.get("INFRAMIND_S3_PREFIX", default_var="raw/")
    max_logs = int(Variable.get("INFRAMIND_MAX_LOGS", default_var="3"))

    try:
        logs, keys = fetch_logs_from_s3(bucket=bucket, prefix=prefix, max_logs=max_logs)
        logs_ingested_total.inc(len(logs))
    except Exception as e:
        logs_fetch_errors_total.inc()
        raise

    logger.info("Fetched %d logs from S3", len(logs))
    context["ti"].xcom_push(key="raw_logs",  value=logs)
    context["ti"].xcom_push(key="s3_keys",   value=keys)
    context["ti"].xcom_push(key="dag_start", value=time.time())
    return logs


def task_normalize_logs(**context) -> list[dict]:
    """
    Task 2 — Normalize each raw log into a structured dict.
    Pushes list of normalized log dicts to XCom.
    """
    from core.normalizer import normalize_log
    from core.metrics import logs_processed_total, log_parse_errors_total

    raw_logs = context["ti"].xcom_pull(key="raw_logs", task_ids="fetch_logs")

    normalized = []
    for raw in raw_logs:
        try:
            n = normalize_log(raw)
            normalized.append({
                "raw":           n.raw,
                "message":       n.message,
                "severity":      n.severity,
                "service":       n.service,
                "source_format": n.source_format,
                "timestamp":     n.timestamp,
            })
            logs_processed_total.inc()
        except Exception as e:
            log_parse_errors_total.inc()
            logger.warning("Failed to normalize log: %s", e)

    logger.info("Normalized %d logs", len(normalized))
    context["ti"].xcom_push(key="normalized_logs", value=normalized)
    return normalized


def task_embed_runbooks(**context):
    """
    Task 3 — Rebuild ChromaDB embeddings if runbooks changed.
    Idempotent — skips chunks already embedded.
    Set Airflow Variable INFRAMIND_FORCE_REBUILD=true to force full rebuild.
    """
    from core.vectordb import build_vector_db

    force = Variable.get("INFRAMIND_FORCE_REBUILD", default_var="false").lower() == "true"
    collection = build_vector_db(force_rebuild=force)

    # Reset force rebuild flag after use
    if force:
        Variable.set("INFRAMIND_FORCE_REBUILD", "false")
        logger.info("Force rebuild complete — reset flag")

    logger.info("ChromaDB ready")
    return "ok"


def task_run_rca(**context):
    """
    Task 4 — Run autonomous RCA workflow on each normalized log.
    Pushes list of RCA result dicts to XCom.
    
    NOTE: XCom has 48KB size limit. If processing large batches (>50 logs),
    consider switching to S3-backed XCom or dynamic task mapping.
    """
    from core.vectordb   import build_vector_db
    from dags.workflow import run_autonomous_workflow
    from core.metrics  import (
        rca_success_total, rca_failure_total,
        rca_generation_latency_seconds, rca_attempts_total, rca_final_score
    )

    normalized_logs = context["ti"].xcom_pull(
        key="normalized_logs", task_ids="normalize_logs"
    ) or []

    if not normalized_logs:
        logger.warning("No normalized logs to process — check fetch_logs and S3 raw/ prefix")
        context["ti"].xcom_push(key="rca_results", value=[])
        return []

    collection = build_vector_db(force_rebuild=False)

    results = []
    for i, log_dict in enumerate(normalized_logs):
        raw_log = log_dict["raw"]
        logger.info("Processing log %d/%d | format=%s severity=%s",
                    i + 1, len(normalized_logs),
                    log_dict["source_format"], log_dict["severity"])
        t0 = time.time()
        try:
            rca, run_id, attempts, score, critic_review, norm = run_autonomous_workflow(
                log_text=raw_log,
                collection=collection,
            )
            rca_generation_latency_seconds.observe(time.time() - t0)
            rca_attempts_total.observe(attempts)
            rca_final_score.observe(score)
            rca_success_total.inc()

            import re
            critic_score = int(score * 10)
            note_match       = re.search(r"NOTE:\s*(.+)", critic_review, re.IGNORECASE)
            critic_reasoning = note_match.group(1).strip() if note_match else re.sub(r"SCORE:\s*\[?\d+\]?\s*\|?\s*", "", critic_review).strip()

            results.append({
                "rca_output": {
                    "incident_id":   rca.incident_id,
                    "summary":       rca.summary,
                    "root_cause":    rca.root_cause,
                    "immediate_fix": rca.immediate_fix,
                    "severity":      rca.severity,
                    "confidence":    rca.confidence_score,
                    "attempts":      attempts,
                    "raw_log":       raw_log,
                    "log_service":   norm.service or "unknown",
                    "log_severity":  norm.severity,
                    "log_format":    norm.source_format,
                    "model_used":    rca.model_used,
                    "mlflow_run_id": run_id,
                },
                "ai_critic": {
                    "score":     critic_score,
                    "reasoning": critic_reasoning,
                },
            })
        except Exception as e:
            rca_failure_total.inc()
            logger.error("RCA failed for log %d: %s", i + 1, e)
            results.append({
                "raw_log": raw_log,
                "status":  "failed",
                "error":   str(e),
            })

    logger.info(
        "RCA complete | success=%d failed=%d",
        sum(1 for r in results if "rca_output" in r),
        sum(1 for r in results if "error" in r),
    )
    context["ti"].xcom_push(key="rca_results", value=results)
    return results


def task_review_sent(**context):
    """
    Task 5 — Fire-and-forget: hand each RCA result off to Step Functions,
    then move processed S3 logs raw/ → processed/.
    """
    from core.sfn_client import trigger_step_function
    from config.config import S3_BUCKET
    from dags.ingestion import move_to_processed

    results  = context["ti"].xcom_pull(key="rca_results", task_ids="run_rca") or []
    s3_keys  = context["ti"].xcom_pull(key="s3_keys",     task_ids="fetch_logs") or []

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

    logger.info("Triggered %d Step Functions execution(s)", sum(1 for r in results if "rca_output" in r))


# DAG DEFINITION
with DAG(
    dag_id="inframind_rca_pipeline",
    default_args=default_args,
    description="Automated RCA pipeline — S3 logs → normalize → embed → RCA → results",
    schedule=None,
    catchup=False,
    tags=["inframind", "llmops", "rca"],
) as dag:

    fetch_logs = PythonOperator(
        task_id="fetch_logs",
        python_callable=task_fetch_logs,
    )

    normalize_logs = PythonOperator(
        task_id="normalize_logs",
        python_callable=task_normalize_logs,
    )

    embed_runbooks = PythonOperator(
        task_id="embed_runbooks",
        python_callable=task_embed_runbooks,
        pool="single_thread_pool",  # Prevents concurrent ChromaDB writes
    )

    run_rca = PythonOperator(
        task_id="run_rca",
        python_callable=task_run_rca,
        execution_timeout=timedelta(minutes=30),  # Prevent indefinite hangs
    )

    review_sent = PythonOperator(
        task_id="review_sent",
        python_callable=task_review_sent,
    )

    # ── Task dependencies ─────────────────────────────────────────────
    [fetch_logs, embed_runbooks] >> normalize_logs >> run_rca >> review_sent
