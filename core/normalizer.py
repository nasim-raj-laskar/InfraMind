"""
core/normalizer.py — Detects log format and normalizes into a consistent structure.
Supports: CloudWatch JSON, k8s syslog, standard app logs, nginx/apache,
          syslog, docker container logs, prefixed plain text, unknown fallback.
"""
import re
import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("inframind.normalizer")


@dataclass
class NormalizedLog:
    timestamp:     Optional[str]
    severity:      str           # ERROR / WARN / INFO / DEBUG
    service:       Optional[str]
    message:       str           # clean core error message
    raw:           str           # always preserve original
    source_format: str           # which parser matched


def normalize_log(raw_log: str) -> NormalizedLog:
    """
    Detects log format and normalizes into a consistent structure.
    Never raises — always returns a NormalizedLog.
    """
    raw_log = raw_log.strip()

    # ── 1. CloudWatch / structured JSON ──────────────────────────────────
    try:
        data = json.loads(raw_log)
        if isinstance(data, dict) and any(k in data for k in ("message", "msg", "@message")):
            msg = data.get("message") or data.get("msg") or data.get("@message", "")
            lvl = (
                data.get("level") or data.get("severity") or
                data.get("log_level") or data.get("levelname") or "ERROR"
            ).upper()
            ts  = (
                data.get("timestamp") or data.get("time") or
                data.get("@timestamp") or data.get("eventTime")
            )
            svc = (
                data.get("service") or data.get("container") or
                data.get("source")  or data.get("logger")
            )
            logger.debug("Parsed as cloudwatch_json")
            return NormalizedLog(
                timestamp=ts, severity=lvl, service=svc,
                message=msg, raw=raw_log, source_format="cloudwatch_json"
            )
    except (json.JSONDecodeError, TypeError):
        pass

    # ── 2. Kubernetes / kubelet syslog ────────────────────────────────────
    # E0115 10:23:45.123456    42 pod_workers.go:191] Error syncing pod
    k8s = re.match(
        r'^([EWID])(\d{4})\s+([\d:.]+)\s+\d+\s+[\w./]+:\d+\]\s+(.+)$',
        raw_log
    )
    if k8s:
        level_map = {"E": "ERROR", "W": "WARN", "I": "INFO", "D": "DEBUG"}
        logger.debug("Parsed as k8s_syslog")
        return NormalizedLog(
            timestamp=k8s.group(2),
            severity=level_map.get(k8s.group(1), "ERROR"),
            service="kubelet", message=k8s.group(4),
            raw=raw_log, source_format="k8s_syslog"
        )

    # ── 3. Standard app log (Python / Java / Node) ───────────────────────
    # 2024-01-15 10:23:45 [ERROR] message
    # 2024-01-15T10:23:45.123Z ERROR message
    app = re.match(
        r'^(\d{4}[-/]\d{2}[-/]\d{2}[T\s][\d:.]+Z?)\s+\[?(\w+)\]?\s+(.+)$',
        raw_log
    )
    if app:
        logger.debug("Parsed as standard_app")
        return NormalizedLog(
            timestamp=app.group(1), severity=app.group(2).upper(),
            service=None, message=app.group(3),
            raw=raw_log, source_format="standard_app"
        )

    # ── 4. Nginx / Apache access log ─────────────────────────────────────
    # 192.168.1.1 - - [15/Jan/2024:10:23:45 +0000] "GET /api/v1 HTTP/1.1" 500 1234
    nginx = re.match(
        r'^(\S+)\s+-\s+-\s+\[([^\]]+)\]\s+"([^"]+)"\s+(\d{3})\s+(\d+)',
        raw_log
    )
    if nginx:
        status   = int(nginx.group(4))
        severity = "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO"
        logger.debug("Parsed as nginx_access")
        return NormalizedLog(
            timestamp=nginx.group(2), severity=severity, service="nginx",
            message=f"HTTP {status} — {nginx.group(3)}",
            raw=raw_log, source_format="nginx_access"
        )

    # ── 5. Syslog format ─────────────────────────────────────────────────
    # Jan 15 10:23:45 hostname servicename[PID]: message
    syslog = re.match(
        r'^(\w{3}\s+\d+\s+[\d:]+)\s+(\S+)\s+([\w/-]+)\[\d+\]:\s+(.+)$',
        raw_log
    )
    if syslog:
        msg      = syslog.group(4)
        severity = (
            "ERROR" if re.search(r'error|fail|fatal|crit', msg, re.I) else
            "WARN"  if re.search(r'warn|warning',          msg, re.I) else "INFO"
        )
        logger.debug("Parsed as syslog")
        return NormalizedLog(
            timestamp=syslog.group(1), severity=severity,
            service=syslog.group(3), message=msg,
            raw=raw_log, source_format="syslog"
        )

    # ── 6. Docker container log ───────────────────────────────────────────
    # 2024-01-15T10:23:45.123456789Z stdout F actual log message
    docker = re.match(
        r'^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\s+\w+\s+\w\s+(.+)$',
        raw_log
    )
    if docker:
        msg      = docker.group(2)
        severity = (
            "ERROR" if re.search(r'error|fail|fatal|exception', msg, re.I) else
            "WARN"  if re.search(r'warn|warning',               msg, re.I) else "INFO"
        )
        logger.debug("Parsed as docker_log")
        return NormalizedLog(
            timestamp=docker.group(1), severity=severity, service=None,
            message=msg, raw=raw_log, source_format="docker_log"
        )

    # ── 7. Prefixed plain text ────────────────────────────────────────────
    # ERROR: message  /  [WARN] message  /  CRITICAL: message
    prefix = re.match(
        r'^\[?(CRITICAL|ERROR|WARN(?:ING)?|INFO|DEBUG)\]?[:\s]+(.+)$',
        raw_log, re.IGNORECASE
    )
    if prefix:
        lvl = prefix.group(1).upper()
        lvl = "WARN" if lvl == "WARNING" else lvl
        logger.debug("Parsed as prefixed_plain")
        return NormalizedLog(
            timestamp=None, severity=lvl, service=None,
            message=prefix.group(2), raw=raw_log, source_format="prefixed_plain"
        )

    # ── 8. Fallback ───────────────────────────────────────────────────────
    severity = (
        "ERROR" if re.search(r'error|fail|fatal|exception|refused|timeout|crash', raw_log, re.I) else
        "WARN"  if re.search(r'warn|warning|slow|high|degraded',                  raw_log, re.I) else
        "INFO"
    )
    logger.debug("Parsed as unknown (fallback)")
    return NormalizedLog(
        timestamp=None, severity=severity, service=None,
        message=raw_log, raw=raw_log, source_format="unknown"
    )


def to_prompt_string(n: NormalizedLog) -> str:
    """Converts a NormalizedLog into a clean structured string for LLM prompts."""
    return (
        f"Timestamp  : {n.timestamp or 'unknown'}\n"
        f"Severity   : {n.severity}\n"
        f"Service    : {n.service or 'unknown'}\n"
        f"Message    : {n.message}\n"
        f"Log format : {n.source_format}\n"
        f"Raw log    : {n.raw}"
    )
