"""
core/evaluator.py — DeepEval wrapper using Mistral-7B as judge.
"""
import logging
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric
from deepeval.test_case import LLMTestCase
from core.bedrock_client import call_mistral
from core.models import RCAOutput

logger = logging.getLogger("inframind.evaluator")


class BedrockMistralJudge(DeepEvalBaseLLM):
    def load_model(self):        return self
    def get_model_name(self):    return "Bedrock Mistral-7B Judge"

    def generate(self, prompt: str) -> str:
        return call_mistral(prompt, max_tokens=1024)

    async def a_generate(self, prompt: str) -> str:
        return self.generate(prompt)


_judge = BedrockMistralJudge()


def run_deepeval(log: str, context: str, rca: RCAOutput) -> tuple[float, float]:
    """
    Returns (faithfulness_score, relevancy_score).
    Returns (0.0, 0.0) on any failure — never raises.
    """
    try:
        test_case    = LLMTestCase(
            input=log,
            actual_output=rca.immediate_fix,
            retrieval_context=[context]
        )
        faith_metric = FaithfulnessMetric(threshold=0.7, model=_judge)
        rel_metric   = AnswerRelevancyMetric(threshold=0.7, model=_judge)

        faith_metric.measure(test_case)
        rel_metric.measure(test_case)

        logger.info(
            "DeepEval | faithfulness=%.2f relevancy=%.2f",
            faith_metric.score, rel_metric.score
        )
        return faith_metric.score, rel_metric.score

    except Exception as e:
        logger.warning("DeepEval evaluation failed: %s", e)
        return 0.0, 0.0
