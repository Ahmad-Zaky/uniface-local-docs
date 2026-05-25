#!/usr/bin/env python3
"""
Generic MCP client — works with any MCP server.

Supports multiple LLM providers via a common LLMBackend interface.

Provider       SDK to install          API key env var          Free tier
----------     --------------------    ---------------------    ---------
groq           pip install groq        GROQ_API_KEY             yes
claude         pip install anthropic   ANTHROPIC_API_KEY        no (cheap)
gemini         pip install             GEMINI_API_KEY           yes
                 google-generativeai
openai         pip install openai      OPENAI_API_KEY           no
<any openai-   pip install openai      OPENAI_API_KEY +         varies
 compatible>                           OPENAI_BASE_URL
github         pip install openai      GITHUB_PAT               yes (free tier)

Usage:
  # Point at any MCP server:
  python client.py --server /path/to/mcp_server/server.py

  # Explicit provider:
  python client.py --server /path/to/server.py --provider groq

  # Custom system prompt:
  python client.py --server /path/to/server.py \\
    --system-prompt "You are a helpful assistant. Use the available tools."

  # Single question and exit:
  python client.py --server /path/to/server.py --prompt "What is X?"

  # Demo mode — runs every line in an examples file:
  python client.py --server /path/to/server.py \\
    --examples /path/to/examples.txt --demo
"""

from __future__ import annotations

import abc
import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ── Default system prompt ───────────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. "
    "Always use the available tools to look up information before answering — "
    "do not rely on prior knowledge."
)


# ── Shared data types ───────────────────────────────────────────────────────

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: str                                    # "user" | "assistant" | "tool"
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_result: str | None = None


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict


# ── LLMBackend ABC ──────────────────────────────────────────────────────────

class LLMBackend(abc.ABC):
    """
    Extend this class to add a new LLM provider.

    Implement:
      complete()   — one round-trip to the LLM; returns (text, tool_calls)
      from_env()   — construct the backend from environment variables
      label        — human-readable name shown in the terminal

    The agent loop in run_agent() calls complete() repeatedly until
    tool_calls is empty, at which point text holds the final answer.
    """

    @abc.abstractmethod
    def complete(
        self,
        system: str,
        history: list[Message],
        tools: list[MCPTool],
    ) -> tuple[str | None, list[ToolCall]]:
        """
        Send the conversation to the LLM and return its response.

        Returns (text, tool_calls). When tool_calls is non-empty the model
        wants to call tools; when it is empty, text is the final answer.
        """

    @classmethod
    @abc.abstractmethod
    def from_env(cls) -> "LLMBackend":
        """Build the backend from environment variables, or raise RuntimeError."""

    @property
    @abc.abstractmethod
    def label(self) -> str:
        """E.g. 'Groq / llama-3.3-70b-versatile'."""


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_python_tag_calls(text: str) -> list[tuple[str, dict]]:
    """Parse <|python_tag|>func({...}) blocks that some Llama variants emit as plain text."""
    results = []
    pattern = re.compile(r'<\|python_tag\|>\s*(\w+)\s*\((\{.*?\})?\s*\)', re.DOTALL)
    for m in pattern.finditer(text):
        args_str = m.group(2) or "{}"
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            args = {}
        results.append((m.group(1), args))
    return results


# ── Groq backend ────────────────────────────────────────────────────────────

class GroqBackend(LLMBackend):
    DEFAULT_MODEL = "llama-3.3-70b-versatile"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        try:
            from groq import Groq
        except ImportError:
            raise RuntimeError("Run: pip install groq")
        self._client = Groq(api_key=api_key)
        self._model = model

    @classmethod
    def from_env(cls) -> "GroqBackend":
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RuntimeError(
                "GROQ_API_KEY is not set.\n"
                "Get a free key at https://console.groq.com → API Keys"
            )
        return cls(key, os.environ.get("GROQ_MODEL", cls.DEFAULT_MODEL))

    @property
    def label(self) -> str:
        return f"Groq / {self._model}"

    def complete(self, system, history, tools):
        messages = [{"role": "system", "content": system}]
        for m in history:
            if m.role == "user":
                messages.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                entry: dict = {"role": "assistant", "content": m.text or ""}
                if m.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in m.tool_calls
                    ]
                messages.append(entry)
            elif m.role == "tool":
                messages.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.tool_result,
                })

        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=_to_openai_tools(tools),
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            return msg.content, [
                ToolCall(tc.id, tc.function.name, json.loads(tc.function.arguments))
                for tc in msg.tool_calls
            ]
        # Llama sometimes emits <|python_tag|>func({...}) as plain text instead
        # of structured tool calls — detect and convert so the agent loop works.
        if msg.content:
            fallback = _parse_python_tag_calls(msg.content)
            if fallback:
                return None, [
                    ToolCall(id=str(uuid.uuid4()), name=name, arguments=args)
                    for name, args in fallback
                ]
        return msg.content, []


# ── Anthropic / Claude backend ──────────────────────────────────────────────

class ClaudeBackend(LLMBackend):
    DEFAULT_MODEL = "claude-haiku-4-5-20251001"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        try:
            import anthropic
            self._lib = anthropic
        except ImportError:
            raise RuntimeError("Run: pip install anthropic")
        self._client = self._lib.Anthropic(api_key=api_key)
        self._model = model

    @classmethod
    def from_env(cls) -> "ClaudeBackend":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set.\n"
                "Get a key at https://console.anthropic.com"
            )
        return cls(key, os.environ.get("CLAUDE_MODEL", cls.DEFAULT_MODEL))

    @property
    def label(self) -> str:
        return f"Anthropic / {self._model}"

    def complete(self, system, history, tools):
        anthropic_msgs: list[dict] = []
        i = 0
        while i < len(history):
            m = history[i]
            if m.role == "user":
                anthropic_msgs.append({"role": "user", "content": m.text})
                i += 1
            elif m.role == "assistant":
                content: list = []
                if m.text:
                    content.append({"type": "text", "text": m.text})
                for tc in m.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                anthropic_msgs.append({"role": "assistant", "content": content})
                i += 1
            elif m.role == "tool":
                results = []
                while i < len(history) and history[i].role == "tool":
                    t = history[i]
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": t.tool_call_id,
                        "content": t.tool_result,
                    })
                    i += 1
                anthropic_msgs.append({"role": "user", "content": results})

        claude_tools = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

        resp = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=anthropic_msgs,
            tools=claude_tools,
        )

        text, calls = None, []
        for block in resp.content:
            if block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, block.input))
            elif block.type == "text":
                text = block.text
        return text, calls


# ── Google Gemini backend ───────────────────────────────────────────────────

class GeminiBackend(LLMBackend):
    DEFAULT_MODEL = "gemini-2.0-flash"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        try:
            from google import genai
            from google.genai import types as genai_types
            self._genai = genai
            self._types = genai_types
        except ImportError:
            raise RuntimeError("Run: pip install google-genai")
        self._client = genai.Client(api_key=api_key)
        self._model_name = model

    @classmethod
    def from_env(cls) -> "GeminiBackend":
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set.\n"
                "Get a free key at https://aistudio.google.com/app/apikey"
            )
        return cls(key, os.environ.get("GEMINI_MODEL", cls.DEFAULT_MODEL))

    @property
    def label(self) -> str:
        return f"Google Gemini / {self._model_name}"

    def _build_gemini_tools(self, tools: list[MCPTool]):
        types = self._types
        type_map = {
            "string": types.Type.STRING,
            "integer": types.Type.INTEGER,
            "number": types.Type.NUMBER,
            "boolean": types.Type.BOOLEAN,
            "object": types.Type.OBJECT,
            "array": types.Type.ARRAY,
        }

        def schema(js: dict) -> "types.Schema":
            props = {
                k: types.Schema(
                    type=type_map.get(v.get("type", "string"), types.Type.STRING),
                    description=v.get("description", ""),
                )
                for k, v in js.get("properties", {}).items()
            }
            return types.Schema(
                type=types.Type.OBJECT,
                properties=props,
                required=js.get("required", []),
            )

        return types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=schema(t.input_schema),
            )
            for t in tools
        ])

    def _to_gemini_contents(self, history: list[Message]):
        types = self._types
        contents = []
        for m in history:
            if m.role == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=m.text)]))
            elif m.role == "assistant":
                parts = []
                if m.text:
                    parts.append(types.Part(text=m.text))
                for tc in m.tool_calls:
                    parts.append(types.Part(
                        function_call=types.FunctionCall(name=tc.name, args=tc.arguments or {})
                    ))
                contents.append(types.Content(role="model", parts=parts))
            elif m.role == "tool":
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(
                        function_response=types.FunctionResponse(
                            name=m.tool_name,
                            response={"result": m.tool_result},
                        )
                    )],
                ))
        return contents

    def complete(self, system, history, tools):
        types = self._types
        contents = self._to_gemini_contents(history)
        resp = self._client.models.generate_content(
            model=self._model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                tools=[self._build_gemini_tools(tools)],
            ),
        )

        text, calls = None, []
        for part in resp.candidates[0].content.parts:
            fc = getattr(part, "function_call", None)
            if fc and fc.name:
                calls.append(ToolCall(id=fc.name, name=fc.name, arguments=dict(fc.args)))
            elif getattr(part, "text", None):
                text = part.text
        return text, calls


# ── OpenAI backend ──────────────────────────────────────────────────────────

class OpenAIBackend(LLMBackend):
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, base_url: str | None = None):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("Run: pip install openai")
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    @classmethod
    def from_env(cls) -> "OpenAIBackend":
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set.\n"
                "Get a key at https://platform.openai.com/api-keys\n"
                "Tip: set OPENAI_BASE_URL to use any OpenAI-compatible endpoint."
            )
        return cls(
            key,
            os.environ.get("OPENAI_MODEL", cls.DEFAULT_MODEL),
            os.environ.get("OPENAI_BASE_URL"),
        )

    @property
    def label(self) -> str:
        return f"OpenAI / {self._model}"

    def complete(self, system, history, tools):
        messages = [{"role": "system", "content": system}]
        for m in history:
            if m.role == "user":
                messages.append({"role": "user", "content": m.text})
            elif m.role == "assistant":
                entry: dict = {"role": "assistant", "content": m.text or ""}
                if m.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in m.tool_calls
                    ]
                messages.append(entry)
            elif m.role == "tool":
                messages.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.tool_result,
                })

        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=_to_openai_tools(tools),
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            return msg.content, [
                ToolCall(tc.id, tc.function.name, json.loads(tc.function.arguments))
                for tc in msg.tool_calls
            ]
        return msg.content, []


# ── GitHub Models backend ───────────────────────────────────────────────────

class GitHubBackend(OpenAIBackend):
    DEFAULT_MODEL = "gpt-4o"
    _BASE_URL = "https://models.inference.ai.azure.com"

    def __init__(self, pat: str, model: str = DEFAULT_MODEL):
        super().__init__(api_key=pat, model=model, base_url=self._BASE_URL)

    @classmethod
    def from_env(cls) -> "GitHubBackend":
        pat = os.environ.get("GITHUB_PAT")
        if not pat:
            raise RuntimeError(
                "GITHUB_PAT is not set.\n"
                "Create a token at https://github.com/settings/personal-access-tokens"
            )
        return cls(pat, os.environ.get("GITHUB_MODEL", cls.DEFAULT_MODEL))

    @property
    def label(self) -> str:
        return f"GitHub Models / {self._model}"


# ── Shared helper ───────────────────────────────────────────────────────────

def _to_openai_tools(tools: list[MCPTool]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


# ── Provider registry ───────────────────────────────────────────────────────

BACKENDS: dict[str, type[LLMBackend]] = {
    "groq":   GroqBackend,
    "claude": ClaudeBackend,
    "gemini": GeminiBackend,
    "openai": OpenAIBackend,
    "github": GitHubBackend,
}

_ENV_KEYS = {
    "groq":   "GROQ_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "github": "GITHUB_PAT",
}


def _auto_detect() -> str:
    for name, var in _ENV_KEYS.items():
        if os.environ.get(var):
            return name
    keys = "\n".join(f"  export {v}=..." for v in _ENV_KEYS.values())
    raise RuntimeError(f"No LLM API key found. Set one of:\n{keys}")


def build_backend(provider: str | None) -> LLMBackend:
    name = provider or _auto_detect()
    cls = BACKENDS.get(name)
    if cls is None:
        raise RuntimeError(f"Unknown provider '{name}'. Choose from: {', '.join(BACKENDS)}")
    return cls.from_env()


# ── Agent loop ──────────────────────────────────────────────────────────────

async def run_agent(
    backend: LLMBackend,
    session: ClientSession,
    prompt: str,
    system_prompt: str,
) -> None:
    raw_tools = (await session.list_tools()).tools
    tools = [
        MCPTool(name=t.name, description=t.description or "", input_schema=t.inputSchema)
        for t in raw_tools
    ]

    history: list[Message] = [Message(role="user", text=prompt)]

    print(f"\n{'═' * 64}")
    print(f"  {prompt}")
    print('═' * 64)

    while True:
        text, tool_calls = backend.complete(system_prompt, history, tools)

        if not tool_calls:
            history.append(Message(role="assistant", text=text))
            print(f"\n{text}\n")
            break

        history.append(Message(role="assistant", text=text, tool_calls=tool_calls))

        for tc in tool_calls:
            arg_str = ", ".join(f"{k}={v!r}" for k, v in (tc.arguments or {}).items())
            print(f"\n  ⚙  {tc.name}({arg_str})")

            result = await session.call_tool(tc.name, tc.arguments)
            content = next(
                (b.text for b in result.content if hasattr(b, "text")), ""
            )
            preview = content[:160].replace("\n", " ")
            print(f"     ↩  {preview}{'…' if len(content) > 160 else ''}")

            history.append(Message(
                role="tool",
                tool_call_id=tc.id,
                tool_name=tc.name,
                tool_result=content,
            ))


# ── Client modes ────────────────────────────────────────────────────────────

async def interactive(
    backend: LLMBackend,
    session: ClientSession,
    system_prompt: str,
    examples: list[str],
    server_label: str,
) -> None:
    print(f"\nMCP Assistant  [{backend.label}]  server: {server_label}")
    print("Commands: 'examples' · 'quit'  |  Ctrl-C to exit\n")
    while True:
        try:
            prompt = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if not prompt:
            continue
        if prompt.lower() in ("quit", "exit", "q"):
            break
        if prompt.lower() == "examples":
            if not examples:
                print("  No examples loaded. Pass --examples <file> to load prompts.")
            else:
                for i, p in enumerate(examples, 1):
                    print(f"  {i:2d}. {p}")
            continue
        if prompt.isdigit() and examples:
            n = int(prompt)
            if 1 <= n <= len(examples):
                prompt = examples[n - 1]
                print(f"  → {prompt}")
        await run_agent(backend, session, prompt, system_prompt)


async def demo(
    backend: LLMBackend,
    session: ClientSession,
    system_prompt: str,
    examples: list[str],
) -> None:
    if not examples:
        print("Demo mode requires an examples file. Pass --examples <file>.")
        return
    print(f"\nDemo mode [{backend.label}] — {len(examples)} prompt(s)\n")
    for prompt in examples:
        await run_agent(backend, session, prompt, system_prompt)


# ── Entry point ─────────────────────────────────────────────────────────────

async def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--server", "-S",
        required=True,
        metavar="PATH",
        help="Path to the MCP server script (e.g. ../DOCUMENTATION/mcp_server/server.py)",
    )
    ap.add_argument(
        "--provider", "-p",
        choices=list(BACKENDS),
        metavar="|".join(BACKENDS),
        help="LLM provider (default: auto-detect from env vars)",
    )
    ap.add_argument(
        "--system-prompt",
        default=DEFAULT_SYSTEM_PROMPT,
        metavar="TEXT",
        help="System prompt for the LLM (default: generic tool-use prompt)",
    )
    ap.add_argument(
        "--examples",
        metavar="FILE",
        help="Text file with one example prompt per line (used by --demo and 'examples' command)",
    )
    ap.add_argument(
        "--demo",
        action="store_true",
        help="Run all prompts from --examples file and exit",
    )
    ap.add_argument(
        "--prompt",
        metavar="TEXT",
        help="Ask a single question and exit",
    )
    args = ap.parse_args()

    server_path = Path(args.server).resolve()
    if not server_path.exists():
        print(f"ERROR: server not found at {server_path}", file=sys.stderr)
        sys.exit(1)

    examples: list[str] = []
    if args.examples:
        examples_path = Path(args.examples)
        if not examples_path.exists():
            print(f"ERROR: examples file not found: {examples_path}", file=sys.stderr)
            sys.exit(1)
        examples = [
            line.strip()
            for line in examples_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    try:
        backend = build_backend(args.provider)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        cwd=str(server_path.parent),
    )

    server_label = server_path.parent.parent.name  # e.g. "DOCUMENTATION" or "DATABASE_SCHEMA"
    print(f"Connecting to MCP server  [{backend.label}]  ({server_label})…")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]
            print(f"Ready — {len(names)} tools: {', '.join(names)}\n")

            if args.prompt:
                await run_agent(backend, session, args.prompt, args.system_prompt)
            elif args.demo:
                await demo(backend, session, args.system_prompt, examples)
            else:
                await interactive(backend, session, args.system_prompt, examples, server_label)


if __name__ == "__main__":
    asyncio.run(main())
