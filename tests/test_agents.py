"""
tests/test_agents.py — Unit tests for agents.
Mocks Bedrock calls so these run without AWS credentials.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from core.models import RCAOutput


MOCK_USAGE = {"tokens_in": 100, "tokens_out": 50, "model_id": "mock", "cost_usd": 0.001}

MOCK_INVESTIGATION = """
Symptoms:
- connection refused error connecting to RDS

Failing Component:
- RDS database instance

Infrastructure Layer:
- Database

Possible Causes:
- RDS instance stopped or rebooting
"""

MOCK_REASONING = """
Root Cause Analysis:
- RDS instance is stopped

Evidence:
- Log shows connection refused

Runbook Reference:
- Issue 1: Connection Refused
"""

MOCK_FIX = """
Immediate Mitigation:
- Run: aws rds start-db-instance --db-instance-identifier inframind-db

Long-term Fix:
- Enable RDS auto-start

Verification Steps:
- aws rds describe-db-instances
"""

MOCK_RCA_JSON = json.dumps({
    "incident_id":      "test-123",
    "severity":         "High",
    "summary":          "RDS instance stopped causing connection refused",
    "root_cause":       "RDS instance is stopped",
    "immediate_fix":    "Start the RDS instance",
    "confidence_score": 0.9,
    "model_used":       "Llama-3-8B",
})


@patch("agents.investigator.call_llama", return_value=(MOCK_INVESTIGATION, MOCK_USAGE))
def test_investigate(mock_call):
    from agents.investigator import investigate
    result, usage = investigate("ERROR: connection refused", "runbook context", "mock-model")
    assert "connection refused" in result
    assert usage["tokens_in"] == 100
    mock_call.assert_called_once()


@patch("agents.root_cause.call_llama", return_value=(MOCK_REASONING, MOCK_USAGE))
def test_infer_root_cause(mock_call):
    from agents.root_cause import infer_root_cause
    result, usage = infer_root_cause(MOCK_INVESTIGATION, "runbook", "mock-model")
    assert "RDS instance" in result
    mock_call.assert_called_once()


@patch("agents.fix_generator.call_llama", return_value=(MOCK_FIX, MOCK_USAGE))
def test_generate_fix(mock_call):
    from agents.fix_generator import generate_fix
    result, usage = generate_fix(MOCK_REASONING, "mock-model")
    assert "Immediate Mitigation" in result
    mock_call.assert_called_once()


@patch("agents.formatter.call_llama", return_value=(MOCK_RCA_JSON, MOCK_USAGE))
def test_format_rca(mock_call):
    from agents.formatter import format_rca
    rca, usage = format_rca(MOCK_REASONING, MOCK_FIX, "incident-abc", "mock-model")
    assert isinstance(rca, RCAOutput)
    assert rca.incident_id      == "incident-abc"   # overridden
    assert rca.severity         == "High"
    assert rca.confidence_score == 0.9


@patch("agents.critic.call_mistral", return_value="SCORE: [9] | NOTE: Accurate diagnosis.")
def test_critique_score(mock_call):
    from agents.critic import critique
    rca = RCAOutput(
        incident_id="x", severity="High",
        summary="test", root_cause="RDS stopped",
        immediate_fix="start rds", confidence_score=0.9,
        model_used="Llama-3-8B"
    )
    text, score = critique(rca, "runbook context")
    assert score == 0.9
    assert "SCORE" in text


@patch("agents.critic.call_mistral", return_value="some response without score")
def test_critique_no_score_defaults_zero(mock_call):
    from agents.critic import critique
    rca = RCAOutput(
        incident_id="x", severity="Low",
        summary="test", root_cause="unknown",
        immediate_fix="check logs", confidence_score=0.5,
        model_used="Llama-3-8B"
    )
    _, score = critique(rca, "context")
    assert score == 0.0
