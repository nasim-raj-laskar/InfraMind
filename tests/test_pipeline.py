"""
tests/test_pipeline.py — Integration tests for the full pipeline.
Mocks all external calls (Bedrock, ChromaDB, MLflow).
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from core.models import RCAOutput

MOCK_RCA = RCAOutput(
    incident_id="test-incident-001",
    severity="High",
    summary="RDS instance stopped causing connection refused",
    root_cause="RDS instance is stopped or rebooting",
    immediate_fix="aws rds start-db-instance --db-instance-identifier inframind-db",
    confidence_score=0.9,
    model_used="Llama-3-8B",
)


@patch("pipeline.workflow.run_rca",       return_value=MOCK_RCA)
@patch("pipeline.workflow.critique",      return_value=("SCORE: [9] | NOTE: Good.", 0.9))
@patch("pipeline.workflow.run_deepeval",  return_value=(0.85, 0.80))
@patch("pipeline.workflow.get_context",   return_value="runbook context text")
@patch("pipeline.workflow.setup_mlflow")
@patch("pipeline.workflow.log_usage")
@patch("pipeline.workflow.log_attempt")
@patch("pipeline.workflow.log_final")
@patch("mlflow.start_run")
@patch("mlflow.active_run", return_value=None)
def test_workflow_success(
    mock_active, mock_run, mock_log_final, mock_log_attempt,
    mock_log_usage, mock_setup, mock_context, mock_eval,
    mock_critique, mock_rca
):
    """Pipeline should return RCA on first attempt when score >= threshold."""
    from dags.workflow import run_autonomous_workflow

    mock_run.return_value.__enter__ = lambda s: MagicMock(info=MagicMock(run_id="run-abc"))
    mock_run.return_value.__exit__  = MagicMock(return_value=False)

    collection = MagicMock()
    rca, run_id, attempts, score = run_autonomous_workflow(
        "ERROR: connection refused", collection
    )

    assert rca.severity         == "High"
    assert rca.confidence_score == 0.9
    assert attempts             == 1


@patch("pipeline.workflow.run_rca",      return_value=MOCK_RCA)
@patch("pipeline.workflow.critique",     side_effect=[
    ("SCORE: [5] | NOTE: Too vague.", 0.5),   # attempt 1 — fail
    ("SCORE: [9] | NOTE: Much better.", 0.9),  # attempt 2 — pass
])
@patch("pipeline.workflow.run_deepeval", return_value=(0.8, 0.8))
@patch("pipeline.workflow.get_context",  return_value="context")
@patch("pipeline.workflow.setup_mlflow")
@patch("pipeline.workflow.log_usage")
@patch("pipeline.workflow.log_attempt")
@patch("pipeline.workflow.log_final")
@patch("mlflow.start_run")
@patch("mlflow.active_run", return_value=None)
def test_workflow_self_correction(
    mock_active, mock_run, mock_log_final, mock_log_attempt,
    mock_log_usage, mock_setup, mock_context, mock_eval,
    mock_critique, mock_rca
):
    """Pipeline should retry when score is below threshold."""
    from dags.workflow import run_autonomous_workflow

    mock_run.return_value.__enter__ = lambda s: MagicMock(info=MagicMock(run_id="run-xyz"))
    mock_run.return_value.__exit__  = MagicMock(return_value=False)

    collection = MagicMock()
    rca, run_id, attempts, score = run_autonomous_workflow(
        "ERROR: connection refused", collection
    )

    assert attempts == 2   # needed self-correction
    assert score    == 0.9
