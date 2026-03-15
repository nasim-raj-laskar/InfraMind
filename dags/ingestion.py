"""
pipeline/ingestion.py — Fetches raw logs from AWS S3.
Used by the Airflow DAG. Can also be run standalone for testing.
"""
import boto3
import logging
from datetime import datetime, timedelta
from config.config import AWS_ACCESS_KEY, AWS_SECRET_KEY, S3_BUCKET, S3_PREFIX

logger = logging.getLogger("inframind.ingestion")

s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
)


def _decode(raw_bytes: bytes) -> str:
    """
    Safely decode bytes to string.
    Handles UTF-16 BOM (0xff 0xfe) which Windows editors produce,
    falls back to UTF-8 with replacement for any other encoding issues.
    """
    if raw_bytes.startswith(b'\xff\xfe') or raw_bytes.startswith(b'\xfe\xff'):
        return raw_bytes.decode("utf-16")
    return raw_bytes.decode("utf-8", errors="replace")


def fetch_logs_from_s3(
    bucket:      str = None,
    prefix:      str = None,
    since_hours: int = 1,
    max_logs:    int = 100,
) -> list[str]:
    """
    Fetches log files from S3 modified within the last since_hours hours.
    Returns a list of raw log strings.
    """
    bucket = bucket or S3_BUCKET
    prefix = prefix or S3_PREFIX
    cutoff = datetime.utcnow() - timedelta(hours=since_hours)

    logger.info("Fetching logs from s3://%s/%s since %s", bucket, prefix, cutoff)

    paginator = s3_client.get_paginator("list_objects_v2")
    pages     = paginator.paginate(Bucket=bucket, Prefix=prefix)

    keys = []
    for page in pages:
        for obj in page.get("Contents", []):
            if obj["LastModified"].replace(tzinfo=None) >= cutoff:
                keys.append(obj["Key"])

    logger.info("Found %d log files in S3", len(keys))
    keys = keys[:max_logs]

    logs = []
    for key in keys:
        try:
            response  = s3_client.get_object(Bucket=bucket, Key=key)
            raw_bytes = response["Body"].read()
            content   = _decode(raw_bytes)
            for line in content.strip().splitlines():
                line = line.strip()
                if line:
                    logs.append(line)
        except Exception as e:
            logger.warning("Failed to fetch s3://%s/%s - %s", bucket, key, e)

    logger.info("Fetched %d log lines total", len(logs))
    return logs


def fetch_logs_by_date(
    date:   str = None,
    source: str = None,
    bucket: str = None,
) -> list[str]:
    """
    Fetch logs by date partition: raw/{date}/{source}/
    More reliable than LastModified for scheduled DAG runs.
    Use context["ds"] from Airflow as the date argument.
    """
    bucket = bucket or S3_BUCKET
    date   = date   or datetime.utcnow().strftime("%Y/%m/%d")
    prefix = f"raw/{date}/{source}/" if source else f"raw/{date}/"

    logger.info("Fetching logs from s3://%s/%s", bucket, prefix)

    paginator = s3_client.get_paginator("list_objects_v2")
    logs      = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if obj["Size"] == 0:
                continue
            try:
                response  = s3_client.get_object(Bucket=bucket, Key=key)
                raw_bytes = response["Body"].read()
                content   = _decode(raw_bytes)
                for line in content.strip().splitlines():
                    line = line.strip()
                    if line:
                        logs.append(line)
                logger.debug("Fetched %s", key)
            except Exception as e:
                logger.warning("Failed to fetch s3://%s/%s - %s", bucket, key, e)

    logger.info("Fetched %d log lines from %s", len(logs), prefix)
    return logs


def fetch_single_log(bucket: str, key: str) -> str:
    """Fetch a single log file from S3 by key."""
    response  = s3_client.get_object(Bucket=bucket, Key=key)
    raw_bytes = response["Body"].read()
    return _decode(raw_bytes)