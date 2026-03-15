"""api/main.py — FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from api.routes import router
from core.vectordb import build_vector_db
from config.config import setup_logging

setup_logging()
logger = logging.getLogger("inframind.api")

# Shared state — collection loaded once at startup
state = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load ChromaDB collection on startup, release on shutdown."""
    logger.info("Loading ChromaDB collection...")
    state["collection"] = build_vector_db(force_rebuild=False)
    logger.info("ChromaDB ready — API is live")
    yield
    logger.info("Shutting down InfraMind API")


app = FastAPI(
    title="InfraMind RCA API",
    description="Automated Root Cause Analysis for infrastructure incidents",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)
app.state.shared = state
