"""Provider-neutral LLM interface.

Every provider implements one call: ``complete(system, messages, images, schema)``. Structured answers are
always validated here with pydantic, whatever the provider claims, and invalid JSON gets one repair round
with the validation errors. That keeps the three backends interchangeable.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from printwright.config import Settings


class LLMError(RuntimeError):
    pass


@dataclass
class Msg:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class LLMResult:
    text: str
    data: dict | None = None
    model: str = ""
    cost_usd: float | None = None
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0


class Provider(ABC):
    name = "base"

    @abstractmethod
    def complete(
        self, system: str, messages: list[Msg], images: list[Path] | None = None, schema: dict | None = None
    ) -> LLMResult: ...


def get_provider(settings: Settings | None = None, name: str | None = None) -> Provider:
    s = settings or Settings.load()
    name = name or s.provider
    if name == "claude-code":
        from printwright.llm.claude_code import ClaudeCodeProvider

        return ClaudeCodeProvider(s)
    if name == "openai":
        from printwright.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(s)
    if name == "anthropic":
        from printwright.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(s)
    if name == "fake":
        from printwright.llm.fake import FakeProvider

        return FakeProvider()
    raise LLMError(f"unknown provider {name!r}")


FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)\s*\n(.*?)```", re.S)


def extract_code(text: str, langs: tuple[str, ...] = ("python", "py", "openscad", "scad")) -> str | None:
    """The longest fenced block in one of `langs` (or an untagged block)."""
    blocks = [(lang.lower(), body) for lang, body in FENCE.findall(text)]
    cands = [b for lang, b in blocks if lang in langs] or [b for lang, b in blocks if not lang]
    return max(cands, key=len).strip() + "\n" if cands else None


def extract_json(text: str) -> dict | None:
    """First JSON object in text (fenced or bare)."""
    for lang, body in FENCE.findall(text):
        if lang.lower() in ("json", ""):
            try:
                v = json.loads(body)
                if isinstance(v, dict):
                    return v
            except json.JSONDecodeError:
                pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        v = json.loads(text[start : i + 1])
                        if isinstance(v, dict):
                            return v
                    except json.JSONDecodeError:
                        break
                    break
        start = text.find("{", start + 1)
    return None


T = TypeVar("T", bound=BaseModel)

JSON_INSTRUCTION = (
    "\n\nReply with ONE JSON object that validates against this JSON Schema. No prose outside the JSON.\n"
    "```json\n{schema}\n```"
)


def complete_model(
    provider: Provider,
    system: str,
    messages: list[Msg],
    model: type[T],
    images: list[Path] | None = None,
    retries: int = 1,
) -> tuple[T, LLMResult]:
    schema = model.model_json_schema()
    sys_prompt = system + JSON_INSTRUCTION.format(schema=json.dumps(schema))
    msgs = list(messages)
    last_err = ""
    for _attempt in range(retries + 1):
        res = provider.complete(sys_prompt, msgs, images=images, schema=schema)
        data = res.data if isinstance(res.data, dict) else extract_json(res.text)
        if data is None:
            last_err = "The reply contained no JSON object."
        else:
            try:
                return model.model_validate(data), res
            except ValidationError as e:
                last_err = str(e)[:4000]
        msgs = msgs + [
            Msg("assistant", res.text or json.dumps(data or {})),
            Msg("user", f"That JSON is invalid:\n{last_err}\nReturn the corrected JSON object only."),
        ]
        images = None  # images were already seen
    raise LLMError(f"{provider.name}: could not get valid {model.__name__} JSON: {last_err}")
