"""
plugins/metrics_server_plugin.py
Starts the Prometheus metrics HTTP server on :9091 when Airflow boots.
Using port 9091 to avoid conflict with any other service on 8000.
"""
from airflow.plugins_manager import AirflowPlugin  # type: ignore
from core.metrics import start_metrics_server


start_metrics_server(port=9091)


class MetricsServerPlugin(AirflowPlugin):
    name = "metrics_server_plugin"
