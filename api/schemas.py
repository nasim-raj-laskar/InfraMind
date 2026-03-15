"""api/schemas.py — Request and response schemas for the API."""
from pydantic import BaseModel, Field
from core.models import RCAOutput


class AnalyzeRequest(BaseModel):
    log: str = Field(description="Raw log string — any format")

    model_config = {
        "json_schema_extra": {
            "example": {
                "log": "ERROR 500: Database connection refused while connecting to rds.cluster-inframind.internal"
            }
        }
    }


class AnalyzeResponse(BaseModel):
    rca:         RCAOutput
    mlflow_run:  str
    attempts:    int
    final_score: float


class HealthResponse(BaseModel):
    status:  str
    version: str
    db_ready: bool
