"""
core/bedrock_client.py — Single Bedrock client instance + model call helpers.
All agents import from here — never create boto3 clients elsewhere.
"""
import json
import boto3
import logging
from config.config import (
    AWS_ACCESS_KEY, AWS_SECRET_KEY, BEDROCK_REGION,
    MODEL_CRITIC_ID, PRICING
)

logger = logging.getLogger("inframind.bedrock")

# ── Singleton client ──────────────────────────────────────────
bedrock_runtime = boto3.client(
    service_name="bedrock-runtime",
    region_name=BEDROCK_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
)


def call_llama(prompt: str, model_id: str, max_tokens: int = 512) -> tuple[str, dict]:
    """
    Call a Llama-3 model on Bedrock.
    Returns (generation_text, usage_dict).
    """
    response = bedrock_runtime.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "prompt":      prompt,
            "max_gen_len": max_tokens,
            "temperature": 0.1,
            "top_p":       0.9,
        })
    )
    result = json.loads(response["body"].read())
    usage  = {
        "tokens_in":  result.get("prompt_token_count", 0),
        "tokens_out": result.get("generation_token_count", 0),
        "model_id":   model_id,
    }
    usage["cost_usd"] = (
        usage["tokens_in"]  * PRICING[model_id]["input"] +
        usage["tokens_out"] * PRICING[model_id]["output"]
    )
    logger.debug(
        "Llama call | model=%s tokens_in=%d tokens_out=%d cost=$%.5f",
        model_id, usage["tokens_in"], usage["tokens_out"], usage["cost_usd"]
    )
    return result["generation"], usage


def call_mistral(prompt: str, max_tokens: int = 512) -> str:
    """
    Call Mistral-7B on Bedrock.
    Mistral uses different prompt format and response keys.
    """
    mistral_prompt = f"<s>[INST] {prompt} [/INST]"
    response = bedrock_runtime.invoke_model(
        modelId=MODEL_CRITIC_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "prompt":      mistral_prompt,
            "max_tokens":  max_tokens,
            "temperature": 0.1,
        })
    )
    result = json.loads(response["body"].read())
    text   = result.get("outputs", [{}])[0].get("text", "")
    logger.debug("Mistral call | tokens_out=%d", len(text.split()))
    return text
