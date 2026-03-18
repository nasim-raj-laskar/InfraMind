"""
core/metrics.py — Central Prometheus metrics registry.
All instrumentation imports from here. Never create metrics elsewhere.
Exposed on :9091/metrics via a background HTTP server started once per process.

Uses prometheus_client multiprocess mode (PROMETHEUS_MULTIPROC_DIR) so that
metrics incremented inside Airflow task subprocesses are visible to the
scrape server running in the plugin/scheduler process.
"""
import os
import logging
from prometheus_client import (
    Counter, Histogram, Gauge,
    REGISTRY, CollectorRegistry,
    multiprocess, make_wsgi_app,
)
from wsgiref.simple_server import make_server, WSGIRequestHandler
import threading

logger = logging.getLogger("inframind.metrics")

_server_started = False


class _SilentHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def start_metrics_server(port: int = 9091):
    """Serve merged multiprocess metrics on :port. Safe to call multiple times."""
    global _server_started
    if _server_started:
        return
    try:
        def _app(environ, start_response):
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
            return make_wsgi_app(registry)(environ, start_response)

        httpd = make_server("", port, _app, handler_class=_SilentHandler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        _server_started = True
        logger.info("Prometheus multiprocess metrics server started on :%d", port)
    except OSError:
        _server_started = True  # port already bound


# ── 1. Ingestion ──────────────────────────────────────────────

logs_ingested_total = Counter(
    "logs_ingested_total",
    "Total number of log lines fetched from S3",
)

logs_fetch_errors_total = Counter(
    "logs_fetch_errors_total",
    "Total number of S3 fetch failures",
)

# ── 2. Log Processing / RCA Pipeline ─────────────────────────

logs_processed_total = Counter(
    "logs_processed_total",
    "Total log lines successfully normalized",
)

log_parse_errors_total = Counter(
    "log_parse_errors_total",
    "Total log lines that failed normalization",
)

rca_success_total = Counter(
    "rca_success_total",
    "Total RCA runs that completed successfully",
)

rca_failure_total = Counter(
    "rca_failure_total",
    "Total RCA runs that failed",
)

rca_generation_latency_seconds = Histogram(
    "rca_generation_latency_seconds",
    "End-to-end RCA generation time per log",
    buckets=[5, 10, 20, 30, 60, 120, 180, 300],
)

rca_attempts_total = Histogram(
    "rca_attempts_total",
    "Number of self-correction attempts per RCA run",
    buckets=[1, 2, 3],
)

rca_final_score = Histogram(
    "rca_final_score",
    "Critic score of the final accepted RCA",
    buckets=[0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0],
)

# ── 3. DAG / Pipeline ─────────────────────────────────────────

dag_runs_success_total = Counter(
    "dag_runs_success_total",
    "Total successful DAG runs",
)

dag_runs_failure_total = Counter(
    "dag_runs_failure_total",
    "Total failed DAG runs",
)

dag_duration_seconds = Histogram(
    "dag_duration_seconds",
    "Total DAG run duration in seconds",
    buckets=[30, 60, 120, 300, 600, 900, 1800],
)

# ── 4. LLM / Bedrock ─────────────────────────────────────────

llm_request_latency_seconds = Histogram(
    "llm_request_latency_seconds",
    "Bedrock API call latency per request",
    labelnames=["model_id", "agent"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30],
)

llm_errors_total = Counter(
    "llm_errors_total",
    "Total Bedrock API errors",
    labelnames=["model_id", "error_type"],
)

llm_timeouts_total = Counter(
    "llm_timeouts_total",
    "Total Bedrock API timeouts",
    labelnames=["model_id"],
)

llm_tokens_in_total = Counter(
    "llm_tokens_in_total",
    "Total input tokens sent to Bedrock",
    labelnames=["model_id"],
)

llm_tokens_out_total = Counter(
    "llm_tokens_out_total",
    "Total output tokens received from Bedrock",
    labelnames=["model_id"],
)

llm_cost_usd_total = Counter(
    "llm_cost_usd_total",
    "Total estimated Bedrock cost in USD",
    labelnames=["model_id"],
)

llm_retries_total = Counter(
    "llm_retries_total",
    "Total LLM self-correction retries triggered",
)
