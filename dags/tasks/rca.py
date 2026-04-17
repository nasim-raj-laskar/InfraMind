from __future__ import annotations
import re
import time
import logging

logger = logging.getLogger("inframind.dag")


def task_run_rca(**context):
    from core.vectordb import build_vector_db
    from dags.workflow import run_autonomous_workflow
    from core.metrics import (
        rca_success_total, rca_failure_total,
        rca_generation_latency_seconds, rca_attempts_total, rca_final_score,
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
                log_text=raw_log, collection=collection,
            )
            rca_generation_latency_seconds.observe(time.time() - t0)
            rca_attempts_total.observe(attempts)
            rca_final_score.observe(score)
            rca_success_total.inc()

            critic_score = int(score * 10)
            note_match = re.search(r"NOTE:\s*(.+)", critic_review, re.IGNORECASE)
            critic_reasoning = (
                note_match.group(1).strip() if note_match
                else re.sub(r"SCORE:\s*\[?\d+\]?\s*\|?\s*", "", critic_review).strip()
            )

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
            results.append({"raw_log": raw_log, "status": "failed", "error": str(e)})

    logger.info("RCA complete | success=%d failed=%d",
                sum(1 for r in results if "rca_output" in r),
                sum(1 for r in results if "error" in r))
    context["ti"].xcom_push(key="rca_results", value=results)
    return results
