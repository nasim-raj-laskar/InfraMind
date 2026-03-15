"""agents/formatter.py"""
import json
import logging
from core.bedrock_client import call_llama
from core.models import RCAOutput, render_prompt

logger = logging.getLogger("inframind.agents.formatter")


def _extract_json(text: str) -> str:
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output")
    return text[start:end + 1]


def format_rca(
    reasoning:   str,
    fix:         str,
    incident_id: str,
    model_id:    str
) -> tuple[RCAOutput, dict]:
    """
    Agent 4 — Converts reasoning + fix into a structured RCAOutput JSON.
    Returns (RCAOutput, usage_dict).
    """
    logger.info("Formatter agent running | model=%s", model_id)

    schema = RCAOutput.model_json_schema()
    prompt = render_prompt(
        "formatter",
        schema=json.dumps(schema),
        incident_id=incident_id,
        reasoning=reasoning,
        fix=fix,
    )

    result, usage = call_llama(prompt, model_id, max_tokens=1024)

    json_text = _extract_json(result.strip())
    data      = json.loads(json_text)
    rca       = RCAOutput.model_validate(data)
    rca.incident_id = incident_id   # always override with our UUID

    logger.info("Formatter produced RCA | severity=%s confidence=%.2f",
                rca.severity, rca.confidence_score)
    return rca, usage
