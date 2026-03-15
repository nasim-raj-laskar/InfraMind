"""agents/root_cause.py"""
import logging
from core.bedrock_client import call_llama
from core.models import render_prompt

logger = logging.getLogger("inframind.agents.root_cause")


def infer_root_cause(investigation: str, context: str, model_id: str) -> tuple[str, dict]:
    """
    Agent 2 — Infers the root cause from investigation summary + runbook context.
    Returns (reasoning_text, usage_dict).
    """
    logger.info("Root cause agent running | model=%s", model_id)
    prompt = render_prompt("root_cause", investigation=investigation, context=context)
    result, usage = call_llama(prompt, model_id, max_tokens=512)
    return result, usage
