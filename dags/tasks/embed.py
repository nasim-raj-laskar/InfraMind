from __future__ import annotations
import logging

from airflow.models import Variable  # type: ignore

logger = logging.getLogger("inframind.dag")


def task_embed_runbooks(**context):
    from core.vectordb import build_vector_db

    force = Variable.get("INFRAMIND_FORCE_REBUILD", default_var="false").lower() == "true"
    build_vector_db(force_rebuild=force)

    if force:
        Variable.set("INFRAMIND_FORCE_REBUILD", "false")
        logger.info("Force rebuild complete — reset flag")

    logger.info("ChromaDB ready")
    return "ok"
