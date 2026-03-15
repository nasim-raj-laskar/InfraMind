"""
config.py — single source of truth for all configuration.
Every module imports from here. Nothing reads YAML directly.
"""
import os
import yaml
import logging
import logging.config
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent.parent   # InfraMind/
_CFG  = Path(__file__).parent          # InfraMind/config/


def _load(filename: str) -> dict:
    with open(_CFG / filename) as f:
        return yaml.safe_load(f)


# ── Raw YAML dicts ────────────────────────────────────────────
_settings = _load("settings.yaml")
_models   = _load("models.yaml")

# ── Convenience accessors ─────────────────────────────────────

# Pipeline
QUALITY_THRESHOLD   = _settings["pipeline"]["quality_threshold"]
MAX_RETRIES         = _settings["pipeline"]["max_retries"]
LOG_SIZE_THRESHOLD  = _settings["pipeline"]["log_size_threshold"]

# VectorDB
CHROMA_PATH         = str(_ROOT / _settings["vectordb"]["path"])
CHROMA_COLLECTION   = _settings["vectordb"]["collection"]
CHUNK_K             = _settings["vectordb"]["chunk_k"]
MAX_DISTANCE        = _settings["vectordb"]["max_distance"]
EMBED_BATCH_SIZE    = _settings["vectordb"]["batch_size"]

# Runbook
RUNBOOK_DIR         = str(_ROOT / _settings["runbook"]["dir"])
RUNBOOK_GLOB        = _settings["runbook"]["glob"]
RUNBOOK_HEADERS     = _settings["runbook"]["headers"]

# MLflow
MLFLOW_EXPERIMENT   = _settings["mlflow"]["experiment_name"]
MLFLOW_URI          = os.getenv("MLFLOW_TRACKING_URI", "")

# Retrieval
RETRIEVAL_SUFFIX    = _settings["retrieval"]["query_suffix"]

# Models
BEDROCK_REGION      = _models["bedrock"]["region"]
MODEL_SMALL_ID      = _models["bedrock"]["llm"]["small"]["id"]
MODEL_SMALL_LABEL   = _models["bedrock"]["llm"]["small"]["label"]
MODEL_LARGE_ID      = _models["bedrock"]["llm"]["large"]["id"]
MODEL_LARGE_LABEL   = _models["bedrock"]["llm"]["large"]["label"]
MODEL_CRITIC_ID     = _models["bedrock"]["llm"]["critic"]["id"]
MODEL_EMBED_ID      = _models["bedrock"]["embeddings"]["id"]
EMBED_MAX_INPUT     = _models["bedrock"]["embeddings"]["max_input"]

PRICING = {
    MODEL_SMALL_ID: _models["bedrock"]["llm"]["small"]["pricing"],
    MODEL_LARGE_ID: _models["bedrock"]["llm"]["large"]["pricing"],
}

# AWS credentials
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

# Airflow
AIRFLOW_DAG_ID   = _settings["airflow"]["dag_id"]
S3_BUCKET        = _settings["airflow"]["s3_bucket"]
S3_PREFIX        = _settings["airflow"]["s3_prefix"]


def setup_logging():
    """Call once at app startup."""
    log_dir = _ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    with open(_CFG / "logging.yaml") as f:
        log_cfg = yaml.safe_load(f)
    log_cfg["handlers"]["file"]["filename"] = str(log_dir / "inframind.log")
    logging.config.dictConfig(log_cfg)


def select_model(log_text: str) -> tuple[str, str]:
    """Returns (model_id, model_label) based on log length."""
    if len(log_text) > LOG_SIZE_THRESHOLD:
        return MODEL_LARGE_ID, MODEL_LARGE_LABEL
    return MODEL_SMALL_ID, MODEL_SMALL_LABEL
