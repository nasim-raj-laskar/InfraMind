FROM astrocrpublic.azurecr.io/runtime:3.1-9

ENV PYTHONPATH="${PYTHONPATH}:/usr/local/airflow"
ENV PROMETHEUS_MULTIPROC_DIR="/tmp/prometheus_multiproc"
RUN mkdir -p /tmp/prometheus_multiproc && chmod 777 /tmp/prometheus_multiproc