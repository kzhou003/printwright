"""Claude Code as the LLM backend (`claude -p`), using your existing Claude login.

Each call is one stateless headless run. To keep it cheap and predictable the run gets our own system
prompt, no MCP servers, no user/project settings and no tools, except Read when images must be viewed.
Measured on Claude Code 2.1: a small call costs ~$0.02 this way versus ~$1.50 with the default setup,
because the default loads the full coding-agent system prompt and every configured MCP server.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from printwright.config import Settings, find_claude
from printwright.llm.base import LLMError, LLMResult, Msg, Provider


def transcript(messages: list[Msg]) -> str:
    if len(messages) == 1:
        return messages[0].content
    parts = ["Conversation so far (oldest first):"]
    for m in messages[:-1]:
        parts.append(f"\n<{m.role}>\n{m.content}\n</{m.role}>")
    parts.append(f"\nLatest user message:\n{messages[-1].content}")
    return "\n".join(parts)


class ClaudeCodeProvider(Provider):
    name = "claude-code"

    def __init__(self, settings: Settings):
        self.s = settings
        exe = find_claude(settings)
        if not exe:
            raise LLMError("The `claude` CLI was not found. Install Claude Code or set claude_code_bin.")
        self.exe = str(exe)

    def command(self, system: str, schema: dict | None, with_images: bool, img_dir: str | None) -> list[str]:
        cmd = [
            self.exe,
            "-p",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--system-prompt",
            system,
        ]
        if with_images:
            cmd += ["--tools", "Read", "--allowedTools", "Read"]
            if img_dir:
                cmd += ["--add-dir", img_dir]
        else:
            cmd += ["--tools", ""]
        if schema:
            cmd += ["--json-schema", json.dumps(schema)]
        if self.s.claude_code_model:
            cmd += ["--model", self.s.claude_code_model]
        if self.s.claude_code_effort:
            cmd += ["--effort", self.s.claude_code_effort]
        if self.s.claude_code_max_budget_usd:
            cmd += ["--max-budget-usd", str(self.s.claude_code_max_budget_usd)]
        return cmd

    def complete(
        self, system: str, messages: list[Msg], images: list[Path] | None = None, schema: dict | None = None
    ) -> LLMResult:
        prompt = transcript(messages)
        img_dir = None
        with tempfile.TemporaryDirectory(prefix="pw-cc-") as tmp:
            if images:
                img_dir = tmp
                listed = []
                for i, p in enumerate(images):
                    dst = Path(tmp) / f"{i:02d}_{Path(p).name}"
                    shutil.copyfile(p, dst)
                    listed.append(str(dst))
                prompt += "\n\nView these images with the Read tool before answering:\n" + "\n".join(listed)
            cmd = self.command(system, schema, bool(images), img_dir)
            t0 = time.time()
            try:
                proc = subprocess.run(
                    cmd, input=prompt, capture_output=True, text=True, cwd=tmp, timeout=1200
                )
            except subprocess.TimeoutExpired as e:
                raise LLMError("claude -p timed out after 20 minutes") from e
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise LLMError(
                f"claude -p failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-1500:]}"
            ) from None
        if out.get("is_error"):
            raise LLMError(f"claude -p error: {out.get('subtype')}: {str(out.get('result'))[:1500]}")
        return LLMResult(
            text=str(out.get("result") or ""),
            data=out.get("structured_output"),
            model=",".join((out.get("modelUsage") or {}).keys())
            or (self.s.claude_code_model or "claude-code"),
            cost_usd=out.get("total_cost_usd"),
            usage=out.get("usage") or {},
            seconds=time.time() - t0,
        )
