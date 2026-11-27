"""OpenAI backend (Responses API). Needs `pip install printwright[openai]` and OPENAI_API_KEY."""

from __future__ import annotations

import base64
import time
from pathlib import Path

from printwright.config import Settings
from printwright.llm.base import LLMError, LLMResult, Msg, Provider


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, settings: Settings):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError("Install the OpenAI SDK: pip install 'printwright[openai]'") from e
        self.s = settings
        self.client = OpenAI()

    def complete(
        self, system: str, messages: list[Msg], images: list[Path] | None = None, schema: dict | None = None
    ) -> LLMResult:
        import openai

        items: list[dict] = []
        for i, m in enumerate(messages):
            if m.role == "assistant":
                items.append({"role": "assistant", "content": m.content})
                continue
            content: list[dict] = [{"type": "input_text", "text": m.content}]
            if images and i == len(messages) - 1:
                for p in images:
                    b64 = base64.b64encode(Path(p).read_bytes()).decode()
                    content.append({"type": "input_image", "image_url": f"data:image/png;base64,{b64}"})
            items.append({"role": "user", "content": content})
        kwargs: dict = {"model": self.s.openai_model, "instructions": system, "input": items}
        if self.s.openai_reasoning_effort:
            kwargs["reasoning"] = {"effort": self.s.openai_reasoning_effort}
        if schema:
            kwargs["text"] = {
                "format": {"type": "json_schema", "name": "result", "schema": schema, "strict": False}
            }
        t0 = time.time()
        try:
            resp = self.client.responses.create(**kwargs)
        except openai.BadRequestError as e:
            if schema and "schema" in str(e).lower():  # schema not accepted: fall back to plain JSON mode
                kwargs["text"] = {"format": {"type": "json_object"}}
                resp = self.client.responses.create(**kwargs)
            else:
                raise LLMError(f"OpenAI request rejected: {e}") from e
        except openai.APIError as e:
            raise LLMError(f"OpenAI API error: {e}") from e
        usage = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
        return LLMResult(
            text=resp.output_text or "", model=self.s.openai_model, usage=usage, seconds=time.time() - t0
        )
