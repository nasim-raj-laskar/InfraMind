"""
core/bedrock_client.py — Single Bedrock client instance + model call helpers.
All agents import from here — never create boto3 clients elsewhere.
"""
import time
import json
import boto3
import logging
from botocore.exceptions import ReadTimeoutError, EndpointResolutionError
from config.config import (
    AWS_ACCESS_KEY, AWS_SECRET_KEY, BEDROCK_REGION,
    MODEL_CRITIC_ID, PRICING
)
from core.metrics import (
    llm_request_latency_seconds, llm_errors_total,
    llm_timeouts_total, llm_tokens_in_total,
    llm_tokens_out_total, llm_cost_usd_total,
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
    t0 = time.time()
    try:
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
    except ReadTimeoutError:
        llm_timeouts_total.labels(model_id=model_id).inc()
        llm_errors_total.labels(model_id=model_id, error_type="timeout").inc()
        raise
    except Exception as e:
        llm_errors_total.labels(model_id=model_id, error_type=type(e).__name__).inc()
        raise
    finally:
        llm_request_latency_seconds.labels(model_id=model_id, agent="llama").observe(time.time() - t0)

    result = json.loads(response["body"].read())
    usage  = {
        "tokens_in":  result.get("prompt_token_count", 0),
        "tokens_out": result.get("generation_token_count", 0),
        "model_id":   model_id,
    }
    usage["cost_usd"] = (
        usage["tokens_in"]  * PRICING.get(model_id, {}).get("input", 0) +
        usage["tokens_out"] * PRICING.get(model_id, {}).get("output", 0)
    )
    llm_tokens_in_total.labels(model_id=model_id).inc(usage["tokens_in"])
    llm_tokens_out_total.labels(model_id=model_id).inc(usage["tokens_out"])
    llm_cost_usd_total.labels(model_id=model_id).inc(usage["cost_usd"])
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
    t0 = time.time()
    try:
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
    except ReadTimeoutError:
        llm_timeouts_total.labels(model_id=MODEL_CRITIC_ID).inc()
        llm_errors_total.labels(model_id=MODEL_CRITIC_ID, error_type="timeout").inc()
        raise
    except Exception as e:
        llm_errors_total.labels(model_id=MODEL_CRITIC_ID, error_type=type(e).__name__).inc()
        raise
    finally:
        llm_request_latency_seconds.labels(model_id=MODEL_CRITIC_ID, agent="mistral").observe(time.time() - t0)

    result = json.loads(response["body"].read())
    text   = result.get("outputs", [{}])[0].get("text", "")
    tokens_out = len(text.split())
    llm_tokens_out_total.labels(model_id=MODEL_CRITIC_ID).inc(tokens_out)
    logger.debug("Mistral call | tokens_out=%d", tokens_out)
    return text
