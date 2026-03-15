"""
core/models.py — Pydantic schemas and prompt template loader.
"""
from pathlib import Path
from pydantic import BaseModel, Field

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class RCAOutput(BaseModel):
    incident_id:      str   = Field(description="Unique ID for the incident")
    severity:         str   = Field(description="Critical, High, Medium, or Low")
    summary:          str   = Field(description="One-sentence summary of what happened")
    root_cause:       str   = Field(description="The primary technical reason for the failure")
    immediate_fix:    str   = Field(description="What to do right now to restore service")
    confidence_score: float = Field(description="AI confidence 0.0 to 1.0")
    model_used:       str   = Field(description="Llama-3-8B or Llama-3-70B")


class AnalyzeRequest(BaseModel):
    """FastAPI request schema."""
    log:          str
    context_hint: str = ""


class AnalyzeResponse(BaseModel):
    """FastAPI response schema."""
    rca:         RCAOutput
    mlflow_run:  str
    attempts:    int
    final_score: float


def load_prompt(name: str) -> str:
    """Load a prompt template from prompts/{name}.txt"""
    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text()


def render_prompt(name: str, **kwargs) -> str:
    """Load and fill a prompt template with keyword arguments."""
    template = load_prompt(name)
    return template.format(**kwargs)
