from __future__ import annotations

from datetime import timedelta

from airflow import DAG                              # type: ignore
from airflow.operators.python import PythonOperator  # type: ignore

from dags.tasks.fetch     import task_fetch_logs
from dags.tasks.normalize import task_normalize_logs
from dags.tasks.embed     import task_embed_runbooks
from dags.tasks.rca       import task_run_rca
from dags.tasks.review    import task_review_sent

default_args = {
    "owner":            "inframind",
    "depends_on_past":  False,
    "email_on_failure": False,
    "email_on_retry":   False,
    "retries":          2,
    "retry_delay":      timedelta(minutes=5),
}

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
        pool="single_thread_pool",
    )

    run_rca = PythonOperator(
        task_id="run_rca",
        python_callable=task_run_rca,
        execution_timeout=timedelta(minutes=30),
    )

    review_sent = PythonOperator(
        task_id="review_sent",
        python_callable=task_review_sent,
    )

    [fetch_logs, embed_runbooks] >> normalize_logs >> run_rca >> review_sent
