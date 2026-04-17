from __future__ import annotations
import logging

logger = logging.getLogger("inframind.dag")


def task_normalize_logs(**context) -> list[dict]:
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
