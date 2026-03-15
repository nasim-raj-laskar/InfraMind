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
import logging
from datetime import datetime, timedelta

from airflow import DAG                                                       #type:ignore
from airflow.operators.python import PythonOperator                           #type:ignore
from airflow.models import Variable                                           #type:ignore

logger = logging.getLogger("inframind.dag")

# ── Default DAG args ──────────────────────────────────────────
default_args = {
    "owner":            "inframind",
    "depends_on_past":  False,
    "email_on_failure": False,
    "email_on_retry":   False,
    "retries":          2,
    "retry_delay":      timedelta(minutes=5),
}


# ════════════════════════════════════════════════════════════════
# TASK FUNCTIONS
# Each function is fully self-contained — imports inside function
# so Airflow workers only load what they need.
# ════════════════════════════════════════════════════════════════

def task_fetch_logs(**context) -> list[str]:
    """
    Task 1 — Fetch raw logs from S3.
    Uses date partition from Airflow execution date for reliable backfilling.
    """
    from dags.ingestion import fetch_logs_by_date

    date_path = context["ds"].replace("-", "/")   # "2024-03-15" → "2024/03/15"
    bucket    = Variable.get("INFRAMIND_S3_BUCKET", default_var="inframind-data-hub")

    logs = fetch_logs_by_date(
        date=date_path,
        bucket=bucket,
    )

    logger.info("Fetched %d logs from S3", len(logs))
    context["ti"].xcom_push(key="raw_logs", value=logs)
    return logs


def task_normalize_logs(**context) -> list[dict]:
    """
    Task 2 — Normalize each raw log into a structured dict.
    Pushes list of normalized log dicts to XCom.
    """
    from core.normalizer import normalize_log

    raw_logs = context["ti"].xcom_pull(key="raw_logs", task_ids="fetch_logs")

    normalized = []
    for raw in raw_logs:
        n = normalize_log(raw)
        normalized.append({
            "raw":           n.raw,
            "message":       n.message,
            "severity":      n.severity,
            "service":       n.service,
            "source_format": n.source_format,
            "timestamp":     n.timestamp,
        })

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

    normalized_logs = context["ti"].xcom_pull(
        key="normalized_logs", task_ids="normalize_logs"
    )

    # Load collection (already embedded in task 3)
    collection = build_vector_db(force_rebuild=False)

    results = []
    for i, log_dict in enumerate(normalized_logs):
        raw_log = log_dict["raw"]
        logger.info("Processing log %d/%d | format=%s severity=%s",
                    i + 1, len(normalized_logs),
                    log_dict["source_format"], log_dict["severity"])
        try:
            rca, run_id, attempts, score = run_autonomous_workflow(
                log_text=raw_log,
                collection=collection,
            )
            results.append({
                "incident_id":   rca.incident_id,
                "severity":      rca.severity,
                "summary":       rca.summary,
                "root_cause":    rca.root_cause,
                "immediate_fix": rca.immediate_fix,
                "confidence":    rca.confidence_score,
                "model_used":    rca.model_used,
                "mlflow_run_id": run_id,
                "attempts":      attempts,
                "final_score":   score,
                "raw_log":       raw_log,
                "status":        "success",
            })
        except Exception as e:
            logger.error("RCA failed for log %d: %s", i + 1, e)
            results.append({
                "raw_log": raw_log,
                "status":  "failed",
                "error":   str(e),
            })

    logger.info(
        "RCA complete | success=%d failed=%d",
        sum(1 for r in results if r["status"] == "success"),
        sum(1 for r in results if r["status"] == "failed"),
    )
    context["ti"].xcom_push(key="rca_results", value=results)
    return results


def task_post_results(**context):
    """
    Task 5 — Push results to downstream systems.
    Currently: saves to S3 as JSON.
    Extend here to add Slack alerts, PagerDuty, webhooks, etc.
    """
    import boto3
    from config.config import AWS_ACCESS_KEY, AWS_SECRET_KEY, S3_BUCKET

    results  = context["ti"].xcom_pull(key="rca_results", task_ids="run_rca")
    run_date = datetime.utcnow().strftime("%Y/%m/%d/%H%M%S")
    key      = f"rca-results/{run_date}/results.json"

    s3 = boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(results, indent=2),
        ContentType="application/json",
    )

    logger.info("Results saved to s3://%s/%s", S3_BUCKET, key)

    # ── Optional: Slack alert for Critical/High severity ──────────────
    slack_webhook = Variable.get("INFRAMIND_SLACK_WEBHOOK", default_var=None)
    if slack_webhook:
        import urllib.request
        critical = [r for r in results
                    if r.get("status") == "success"
                    and r.get("severity") in ("Critical", "High")]
        if critical:
            msg = {
                "text": f":rotating_light: *InfraMind* — {len(critical)} "
                        f"Critical/High incident(s) detected\n" +
                        "\n".join(
                            f"• `{r['incident_id'][:8]}` [{r['severity']}] {r['summary']}"
                            for r in critical
                        )
            }
            req = urllib.request.Request(
                slack_webhook,
                data=json.dumps(msg).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req)
            logger.info("Slack alert sent for %d critical incidents", len(critical))

    return key


# ════════════════════════════════════════════════════════════════
# DAG DEFINITION
# ════════════════════════════════════════════════════════════════
with DAG(
    dag_id="inframind_rca_pipeline",
    default_args=default_args,
    description="Automated RCA pipeline — S3 logs → normalize → embed → RCA → results",
    schedule_interval="@hourly",
    start_date=datetime(2024, 1, 1),
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

    post_results = PythonOperator(
        task_id="post_results",
        python_callable=task_post_results,
    )

    # ── Task dependencies ─────────────────────────────────────────────
    # fetch_logs and embed_runbooks run in parallel (independent)
    # run_rca waits for both before starting
    [fetch_logs, embed_runbooks] >> normalize_logs >> run_rca >> post_results
