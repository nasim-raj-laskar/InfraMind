"""agents/fix_generator.py"""
import logging
from core.bedrock_client import call_llama
from core.models import render_prompt

logger = logging.getLogger("inframind.agents.fix_generator")


def generate_fix(reasoning: str, model_id: str) -> tuple[str, dict]:
    """
    Agent 3 — Generates concrete remediation steps from root cause reasoning.
    Returns (fix_text, usage_dict).
    """
    logger.info("Fix generator agent running | model=%s", model_id)
    prompt = render_prompt("fix", reasoning=reasoning)
    result, usage = call_llama(prompt, model_id, max_tokens=512)
    return result, usage
