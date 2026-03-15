"""agents/critic.py"""
import re
import logging
from core.bedrock_client import call_mistral
from core.models import RCAOutput, render_prompt

logger = logging.getLogger("inframind.agents.critic")


def critique(rca: RCAOutput, runbook_context: str) -> tuple[str, float]:
    """
    Agent 5 — Senior SRE critic. Uses Mistral-7B to review the RCA report.
    Returns (critique_text, score_0_to_1).
    """
    logger.info("Critic agent running")

    prompt       = render_prompt(
        "critic",
        runbook_context=runbook_context,
        rca_json=rca.model_dump_json(),
    )
    critique_text = call_mistral(prompt, max_tokens=512)

    # Parse SCORE: [X] from response
    match = re.search(r"score\s*:\s*\[?(\d+)\]?", critique_text, re.IGNORECASE)
    score = float(match.group(1)) / 10.0 if match else 0.0

    logger.info("Critic score: %.1f/10 (%.2f)", score * 10, score)
    return critique_text, score
