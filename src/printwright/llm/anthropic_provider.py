"""Anthropic API backend. Needs `pip install printwright[anthropic]` and ANTHROPIC_API_KEY (or `ant auth login`).

Streams every request (CAD scripts can be long) with adaptive thinking and an explicit effort level. On
models that support it, the server-side refusal fallback is enabled so a declined request is retried on
another model inside the same call.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from printwright.config import Settings
from printwright.llm.base import LLMError, LLMResult, Msg, Provider

FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("Install the Anthropic SDK: pip install 'printwright[anthropic]'") from e
        self.s = settings
        self.client = anthropic.Anthropic()

    def complete(
        self, system: str, messages: list[Msg], images: list[Path] | None = None, schema: dict | None = None
    ) -> LLMResult:
        import anthropic

        msgs: list[dict] = []
        for i, m in enumerate(messages):
            if m.role == "user" and images and i == len(messages) - 1:
                blocks: list[dict] = []
                for p in images:
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.standard_b64encode(Path(p).read_bytes()).decode(),
                            },
                        }
                    )
                blocks.append({"type": "text", "text": m.content})
                msgs.append({"role": "user", "content": blocks})
            else:
                msgs.append({"role": m.role, "content": m.content})
        kwargs: dict = {
            "model": self.s.anthropic_model,
            "max_tokens": 64000,
            "system": system,
            "messages": msgs,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.s.anthropic_effort},
        }
        if self.s.anthropic_model in FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        t0 = time.time()
        try:
            with self.client.beta.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError(
                "Anthropic authentication failed: set ANTHROPIC_API_KEY or run `ant auth login`."
            ) from e
        except anthropic.RateLimitError as e:
            raise LLMError(f"Anthropic rate limit: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"Cannot reach the Anthropic API: {e}") from e
        if msg.stop_reason == "refusal":
            cat = getattr(getattr(msg, "stop_details", None), "category", None)
            raise LLMError(f"The model declined this request (category: {cat}).")
        text = "".join(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason == "max_tokens":
            raise LLMError("The reply hit max_tokens and is incomplete.")
        return LLMResult(
            text=text,
            model=msg.model,
            usage=msg.usage.model_dump() if msg.usage else {},
            seconds=time.time() - t0,
        )
