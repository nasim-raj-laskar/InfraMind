"""
pipeline/workflow.py — Main RCA workflow orchestrator.
Calls agents in order, handles self-correction loop, logs to MLflow.
This file contains NO business logic — it only wires agents together.
"""
import uuid
import logging
import mlflow

from config.config import MAX_RETRIES, QUALITY_THRESHOLD, select_model
from core.normalizer import normalize_log, to_prompt_string
from core.vectordb   import get_context, build_retrieval_query
from core.tracker    import (
    setup_mlflow, log_usage, log_attempt,
    log_final, run_name
)
from core.evaluator  import run_deepeval
from core.models     import RCAOutput

from agents.investigator  import investigate
from agents.root_cause    import infer_root_cause
from agents.fix_generator import generate_fix
from agents.formatter     import format_rca
from agents.critic        import critique

logger = logging.getLogger("inframind.pipeline")


def run_rca(
    raw_log:    str,
    context:    str,
    feedback:   str = "",
) -> RCAOutput:
    """
    Single RCA pass — runs all 4 generation agents.
    Called inside run_autonomous_workflow for each attempt.
    """
    incident_id    = str(uuid.uuid4())
    model_id, label = select_model(raw_log)

    normalized     = normalize_log(raw_log)
    structured_log = to_prompt_string(normalized)

    logger.info("Starting RCA | format=%s severity=%s model=%s",
                normalized.source_format, normalized.severity, label)

    # Agent 1 — Investigate
    investigation, u1 = investigate(structured_log, context, model_id)
    log_usage(u1)

    # Inject feedback from previous attempt if available
    if feedback:
        investigation += f"\n\nSenior SRE critique from previous attempt:\n{feedback}"

    # Agent 2 — Root cause
    reasoning, u2 = infer_root_cause(investigation, context, model_id)
    log_usage(u2)

    # Agent 3 — Fix
    fix, u3 = generate_fix(reasoning, model_id)
    log_usage(u3)

    # Agent 4 — Format
    rca, u4 = format_rca(reasoning, fix, incident_id, model_id)
    log_usage(u4)

    rca.model_used = label
    return rca


def run_autonomous_workflow(
    log_text:   str,
    collection,                    # ChromaDB collection
    max_retries: int = None,
) -> tuple[RCAOutput, str, int, float]:
    """
    Full autonomous workflow with self-correction loop.
    Returns (rca, mlflow_run_id, attempts, final_score).
    """
    setup_mlflow()
    max_retries = max_retries or MAX_RETRIES

    if mlflow.active_run():
        mlflow.end_run()

    with mlflow.start_run(run_name=run_name()) as run:
        run_id = run.info.run_id

        # Normalize for retrieval query
        normalized = normalize_log(log_text)
        query      = build_retrieval_query(normalized.severity, normalized.message)
        knowledge  = get_context(query, collection)

        logger.info("Retrieved context | chars=%d", len(knowledge))

        current_feedback = ""
        final_rca        = None
        final_score      = 0.0
        attempts         = 0

        for attempt in range(1, max_retries + 2):
            attempts = attempt
            logger.info("Attempt %d/%d", attempt, max_retries + 1)

            # Generate RCA
            rca = run_rca(log_text, knowledge, current_feedback)

            # Critic review
            critique_text, score = critique(rca, knowledge)
            logger.info("Critique score: %.2f | threshold: %.2f", score, QUALITY_THRESHOLD)

            # DeepEval
            faith, relevancy = run_deepeval(log_text, knowledge, rca)

            # Log this attempt
            log_attempt(attempt, rca, critique_text, score, faith, relevancy)

            final_rca   = rca
            final_score = score

            if score >= QUALITY_THRESHOLD:
                logger.info("Quality threshold met at attempt %d", attempt)
                break
            else:
                logger.warning(
                    "Score %.2f below threshold %.2f — triggering self-correction",
                    score, QUALITY_THRESHOLD
                )
                current_feedback = critique_text

        # Log final run summary
        log_final(
            rca=final_rca,
            critique=critique_text,
            context=knowledge,
            log_text=log_text,
            log_format=normalized.source_format,
            log_severity=normalized.severity,
            log_service=normalized.service or "unknown",
            attempts=attempts,
            final_score=final_score,
        )

        if attempts > max_retries + 1:
            logger.warning("Max retries reached — returning best effort RCA")

        return final_rca, run_id, attempts, final_score, critique_text, normalized
