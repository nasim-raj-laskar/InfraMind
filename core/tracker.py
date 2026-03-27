"""
core/tracker.py — All MLflow tracking helpers.
Agents never call mlflow directly — they return usage dicts,
the workflow passes them here.
"""
import os
import logging
import mlflow
from datetime import datetime
from config.config import MLFLOW_EXPERIMENT, MLFLOW_URI
from core.models import RCAOutput

logger = logging.getLogger("inframind.tracker")

os.environ["MLFLOW_TRACKING_USERNAME"] = os.getenv("DAGSHUB_USERNAME", "")
os.environ["MLFLOW_TRACKING_PASSWORD"] = os.getenv("DAGSHUB_TOKEN", "")


def setup_mlflow():
    # Re-read credentials here in case .env wasn't loaded at import time
    os.environ["MLFLOW_TRACKING_USERNAME"] = os.getenv("DAGSHUB_USERNAME", "")
    os.environ["MLFLOW_TRACKING_PASSWORD"] = os.getenv("DAGSHUB_TOKEN", "")
    mlflow.set_tracking_uri(MLFLOW_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT)
    logger.info("MLflow tracking URI: %s", MLFLOW_URI)


# Accumulators keyed by active run_id so concurrent runs don't bleed into each other
_run_totals: dict = {}


def log_usage(usage: dict):
    """Accumulate token counts and cost — flushed to MLflow in log_final."""
    run = mlflow.active_run()
    if not run:
        return
    rid = run.info.run_id
    t = _run_totals.setdefault(rid, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0})
    t["tokens_in"]  += usage.get("tokens_in",  0)
    t["tokens_out"] += usage.get("tokens_out", 0)
    t["cost_usd"]   += usage.get("cost_usd",   0.0)


def log_attempt(attempt: int, rca: RCAOutput, critique: str,
                score: float, faith: float, relevancy: float):
    """Log all metrics for one pipeline attempt."""
    mlflow.log_metric(f"confidence_score_attempt_{attempt}", rca.confidence_score)
    mlflow.log_metric(f"attempt_{attempt}_score",            score)
    mlflow.log_metric(f"faithfulness_score_attempt_{attempt}", faith)
    mlflow.log_metric(f"relevancy_score_attempt_{attempt}",    relevancy)
    mlflow.log_metric("rca_length", len(rca.summary))

    mlflow.log_dict({
        "incident_id":   rca.incident_id,
        "severity":      rca.severity,
        "summary":       rca.summary,
        "root_cause":    rca.root_cause,
        "immediate_fix": rca.immediate_fix,
    }, f"rca_output_attempt_{attempt}.json")

    mlflow.log_text(critique, f"critique_attempt_{attempt}.txt")


def log_final(rca: RCAOutput, critique: str, context: str,
              log_text: str, log_format: str, log_severity: str,
              log_service: str, attempts: int, final_score: float):
    """Log final run summary."""
    mlflow.log_param("incident_log",           log_text)
    mlflow.log_param("log_format",             log_format)
    mlflow.log_param("log_severity",           log_severity)
    mlflow.log_param("log_service",            log_service)
    mlflow.log_param("total_attempts",         attempts)
    mlflow.log_metric("final_score",           final_score)
    mlflow.log_text(context,                   "retrieved_context.txt")
    mlflow.log_param("retrieved_context_length", len(context))
    mlflow.log_dict(rca.model_dump(),          "final_rca_output.json")
    mlflow.log_text(critique,                  "final_senior_sre_review.txt")

    run = mlflow.active_run()
    if run:
        rid = run.info.run_id
        totals = _run_totals.pop(rid, {})
        if totals:
            mlflow.log_metrics({
                "total_tokens_in":  totals["tokens_in"],
                "total_tokens_out": totals["tokens_out"],
                "total_cost_usd":   totals["cost_usd"],
            })


def run_name() -> str:
    return f"Incident_{datetime.now().strftime('%H%M%S')}"
