"""agents/investigator.py"""
import logging
from core.bedrock_client import call_llama
from core.models import render_prompt

logger = logging.getLogger("inframind.agents.investigator")


def investigate(log: str, context: str, model_id: str) -> tuple[str, dict]:
    """
    Agent 1 — Investigates the log and identifies symptoms, failing component,
    infrastructure layer, and possible causes.
    Returns (investigation_text, usage_dict).
    """
    logger.info("Investigator agent running | model=%s", model_id)
    prompt = render_prompt("investigate", context=context, log=log)
    result, usage = call_llama(prompt, model_id, max_tokens=512)
    return result, usage
