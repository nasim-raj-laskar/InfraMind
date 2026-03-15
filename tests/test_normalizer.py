"""tests/test_normalizer.py — Unit tests for the log normalizer."""
import pytest
from core.normalizer import normalize_log, to_prompt_string


def test_cloudwatch_json():
    log = '{"timestamp":"2024-01-15T10:23:45Z","level":"ERROR","service":"api","message":"connection refused"}'
    n   = normalize_log(log)
    assert n.source_format == "cloudwatch_json"
    assert n.severity       == "ERROR"
    assert n.service        == "api"
    assert "connection refused" in n.message


def test_k8s_syslog():
    log = "E0115 10:23:45.123456   42 pod_workers.go:191] Error syncing pod: failed to pull image"
    n   = normalize_log(log)
    assert n.source_format == "k8s_syslog"
    assert n.severity       == "ERROR"
    assert n.service        == "kubelet"


def test_standard_app_log():
    log = "2024-01-15 10:23:45 [ERROR] DatabaseException: connection refused"
    n   = normalize_log(log)
    assert n.source_format == "standard_app"
    assert n.severity       == "ERROR"


def test_nginx_access_log():
    log = '192.168.1.1 - - [15/Jan/2024:10:23:45 +0000] "GET /api/v1 HTTP/1.1" 500 1234'
    n   = normalize_log(log)
    assert n.source_format == "nginx_access"
    assert n.severity       == "ERROR"
    assert n.service        == "nginx"
    assert "500" in n.message


def test_nginx_4xx_is_warn():
    log = '192.168.1.1 - - [15/Jan/2024:10:23:45 +0000] "GET /api HTTP/1.1" 404 100'
    n   = normalize_log(log)
    assert n.severity == "WARN"


def test_prefixed_plain():
    log = "ERROR 500: Database connection refused"
    n   = normalize_log(log)
    assert n.source_format == "prefixed_plain"
    assert n.severity       == "ERROR"


def test_fallback_detects_error():
    log = "something went wrong with a fatal crash"
    n   = normalize_log(log)
    assert n.severity == "ERROR"


def test_fallback_detects_warn():
    log = "high memory usage detected on node"
    n   = normalize_log(log)
    assert n.severity == "WARN"


def test_to_prompt_string():
    log    = "ERROR: connection refused"
    n      = normalize_log(log)
    result = to_prompt_string(n)
    assert "Severity" in result
    assert "Message"  in result
    assert "Raw log"  in result


def test_never_raises():
    """Normalizer must never raise — even on garbage input."""
    for bad_input in ["", "   ", "\n\n", "!!!@@@###", None]:
        try:
            n = normalize_log(bad_input or "")
            assert n is not None
        except Exception as e:
            pytest.fail(f"normalize_log raised on input {bad_input!r}: {e}")
