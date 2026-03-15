"""api/routes.py — API route definitions."""
import logging
from fastapi import APIRouter, HTTPException, Request
from api.schemas import AnalyzeRequest, AnalyzeResponse, HealthResponse
from dags.workflow import run_autonomous_workflow

logger = logging.getLogger("inframind.api.routes")
router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    """Health check — confirms API and ChromaDB are ready."""
    collection = request.app.state.shared.get("collection")
    return HealthResponse(
        status="ok",
        version="1.0.0",
        db_ready=collection is not None,
    )


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(body: AnalyzeRequest, request: Request):
    """
    Main endpoint — accepts a raw log string and returns a full RCA report.

    Accepts any log format:
    - Plain text error strings
    - Kubernetes syslog
    - CloudWatch JSON
    - Nginx/Apache access logs
    - Standard app logs (Python/Java/Node)
    """
    collection = request.app.state.shared.get("collection")
    if not collection:
        raise HTTPException(status_code=503, detail="Vector DB not ready")

    logger.info("POST /analyze | log_len=%d", len(body.log))

    try:
        rca, run_id, attempts, score = run_autonomous_workflow(
            log_text=body.log,
            collection=collection,
        )
        return AnalyzeResponse(
            rca=rca,
            mlflow_run=run_id,
            attempts=attempts,
            final_score=score,
        )
    except Exception as e:
        logger.error("RCA pipeline failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rebuild-db")
async def rebuild_db(request: Request):
    """
    Force rebuild ChromaDB embeddings.
    Call this after adding new runbook files.
    """
    from core.vectordb import build_vector_db
    logger.info("Rebuilding ChromaDB...")
    collection = build_vector_db(force_rebuild=True)
    request.app.state.shared["collection"] = collection
    return {"status": "rebuilt"}
