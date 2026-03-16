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
    """Decode bytes, stripping UTF-8 BOM (\ufeff) and UTF-16 BOM automatically."""
    if raw_bytes.startswith(b'\xff\xfe') or raw_bytes.startswith(b'\xfe\xff'):
        return raw_bytes.decode("utf-16").replace("\ufeff", "")
    return raw_bytes.decode("utf-8-sig", errors="replace")  # utf-8-sig strips \ufeff


def fetch_logs_from_s3(
    bucket:      str = None,
    prefix:      str = None,
    since_hours: int = None,
    max_logs:    int = 3,
) -> tuple[list[str], list[str]]:
    """
    Fetches the latest max_logs files from S3 sorted by LastModified descending.
    Returns (log_lines, s3_keys) — keys are used later to move files to processed/.
    """
    bucket = bucket or S3_BUCKET
    prefix = prefix or S3_PREFIX
    cutoff = datetime.utcnow() - timedelta(hours=since_hours) if since_hours else None

    logger.info("Fetching latest %d logs from s3://%s/%s", max_logs, bucket, prefix)

    paginator   = s3_client.get_paginator("list_objects_v2")
    all_objects = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Size"] == 0:
                continue
            if cutoff and obj["LastModified"].replace(tzinfo=None) < cutoff:
                continue
            all_objects.append(obj)

    all_objects.sort(key=lambda o: o["LastModified"], reverse=True)
    selected = all_objects[:max_logs]
    keys     = [o["Key"] for o in selected]

    logger.info("Selected %d log files: %s", len(keys), keys)

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
    return logs, keys


def move_to_processed(bucket: str, keys: list[str]):
    """
    Moves processed log files from raw/ to processed/ in S3.
    Prevents the same log from being picked up in a future DAG run.
    """
    for key in keys:
        dest = key.replace("raw/", "processed/", 1)
        try:
            s3_client.copy_object(
                Bucket=bucket,
                CopySource={"Bucket": bucket, "Key": key},
                Key=dest,
            )
            s3_client.delete_object(Bucket=bucket, Key=key)
            logger.info("Moved s3://%s/%s → %s", bucket, key, dest)
        except Exception as e:
            logger.warning("Failed to move %s: %s", key, e)


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