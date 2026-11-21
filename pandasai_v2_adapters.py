"""Small adapters that let the app use PandasAI v2 (SmartDataframe) with LiteLLM.

PandasAI v2 has no official LiteLLM extension (``pandasai-litellm`` is v3-only),
so ``LiteLLM`` below implements v2's ``LLM`` interface on top of ``litellm``.
That keeps the app provider-agnostic: any LiteLLM model string + matching API
key in ``.env`` works (OpenAI, Anthropic, Gemini, ...).
"""

import logging
from typing import Any

import litellm
from litellm import completion
from pandasai.llm.base import LLM
from pandasai.pipelines.pipeline_context import PipelineContext
from pandasai.prompts.base import BasePrompt
from pandasai.responses.response_parser import ResponseParser

# Keep the terminal readable: no per-request INFO lines from LiteLLM.
litellm.suppress_debug_info = True
logging.getLogger("LiteLLM").setLevel(logging.WARNING)


class LiteLLM(LLM):
    """PandasAI v2 LLM backed by ``litellm.completion``."""

    def __init__(self, model: str, **params: Any):
        self.model = model
        self.params = params

    @property
    def type(self) -> str:
        return "litellm"

    def call(self, instruction: BasePrompt, context: PipelineContext = None) -> str:
        self.last_prompt = instruction.to_string()
        memory = context.memory if context else None

        # Same message layout as PandasAI v2's built-in OpenAI client:
        # system prompt + previous turns from memory, then the current prompt.
        messages = memory.to_openai_messages() if memory else []
        messages.append({"role": "user", "content": self.last_prompt})

        response = completion(model=self.model, messages=messages, **self.params)
        return response.choices[0].message.content


class TypedResponseParser(ResponseParser):
    """Return PandasAI's raw ``{"type": ..., "value": ...}`` result.

    The default v2 parser returns only the value, which loses whether it was a
    number, string, dataframe or plot. Keeping the type lets the app render each
    answer correctly (text, ``st.dataframe`` or ``st.image``).
    """

    def parse(self, result: dict) -> dict:
        return {"type": result.get("type"), "value": result.get("value")}
