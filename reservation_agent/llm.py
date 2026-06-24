"""Optional LLM brain — provider-agnostic tool-calling (Anthropic or OpenAI).

This path is DORMANT unless an API key is present and the matching SDK is
installed; the demo runs entirely on the deterministic brain (nlu.py). The
business logic is identical either way — the LLM only orchestrates the same
tools.py functions and phrases replies, so it can never break a business rule.

Selection: ANTHROPIC_API_KEY -> Anthropic; else OPENAI_API_KEY -> OpenAI; else
None (caller falls back to the deterministic brain). SDKs are imported lazily so
the core install stays lean (see requirements.txt).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from . import clock, config, tools
from .session import ConversationState, Reply

log = logging.getLogger("bella_vista.llm")

MAX_TOOL_ITERATIONS = 6
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def build_system_prompt() -> str:
    today = clock.today()
    first, last = config.SLOT_STRINGS[0], config.SLOT_STRINGS[-1]
    return (
        f"You are the friendly reservations assistant for {config.RESTAURANT_NAME}, "
        f"a 60-seat Italian restaurant. Today is {today.isoformat()} "
        f"({today.strftime('%A')}).\n\n"
        "Your ONLY job is booking, changing, cancelling, and looking up table "
        "reservations. Politely decline anything else (menu, prices, ordering food, "
        f"allergen details, unrelated topics) and suggest calling {config.RESTAURANT_PHONE}.\n\n"
        "Rules you must respect (the tools also enforce them):\n"
        f"- Open Tue–Sun, seatings {first}–{last} in 30-minute slots. Closed Monday.\n"
        "- Party size 1–8; larger parties must call.\n"
        "- No past dates; at most 60 days ahead.\n\n"
        "Behaviour:\n"
        "- Resolve relative dates ('tomorrow', 'next Friday') yourself using today's "
        "date, then echo the absolute date back so the guest can confirm.\n"
        "- Pass tools an explicit date (YYYY-MM-DD) and time (24h HH:MM on a slot).\n"
        "- ALWAYS call check_availability before create_reservation, and collect a "
        "name and phone first.\n"
        "- When a slot is full, offer the real alternatives the tool returns; never "
        "invent availability.\n"
        "- Confirm with the guest before cancelling.\n"
        "- For special requests, note them but never promise specific seating.\n"
        "- After booking, state the confirmation code clearly (e.g. BV-A4F2)."
    )


def select_brain() -> Optional["LLMBrain"]:
    """Return an LLM brain if a key + SDK are available, else None."""
    model_override = os.environ.get("BV_LLM_MODEL")
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            log.warning("ANTHROPIC_API_KEY set but `anthropic` not installed — "
                        "falling back to the deterministic brain. (pip install anthropic)")
        else:
            return LLMBrain(AnthropicProvider(model_override or DEFAULT_ANTHROPIC_MODEL))
    if os.environ.get("OPENAI_API_KEY"):
        try:
            import openai  # noqa: F401
        except ImportError:
            log.warning("OPENAI_API_KEY set but `openai` not installed — "
                        "falling back to the deterministic brain. (pip install openai)")
        else:
            return LLMBrain(OpenAIProvider(model_override or DEFAULT_OPENAI_MODEL))
    return None


class LLMBrain:
    name = "llm"

    def __init__(self, provider):
        self.provider = provider

    def respond(self, state: ConversationState, message: str, agent) -> Reply:
        return self.provider.run(state, message, agent)


def _anthropic_tools() -> list[dict]:
    return [
        {"name": s["name"], "description": s["description"], "input_schema": s["parameters"]}
        for s in tools.TOOL_SPECS
    ]


class AnthropicProvider:
    def __init__(self, model: str):
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def run(self, state: ConversationState, message: str, agent) -> Reply:
        messages = state.context.setdefault("llm_messages", [])
        messages.append({"role": "user", "content": message})
        tool_defs = _anthropic_tools()

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=build_system_prompt(),
                tools=tool_defs,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if b.type == "text").strip()
                return Reply(text or "Sorry, could you say that another way?")
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                content, is_error = agent.call_tool_json(block.name, block.input)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": results})
        return Reply("I'm having trouble completing that — could you try rephrasing?")


def _openai_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": s["name"], "description": s["description"], "parameters": s["parameters"],
        }}
        for s in tools.TOOL_SPECS
    ]


class OpenAIProvider:
    def __init__(self, model: str):
        self.model = model
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import openai
            self._client = openai.OpenAI()
        return self._client

    def run(self, state: ConversationState, message: str, agent) -> Reply:
        import json
        messages = state.context.setdefault("llm_messages", None)
        if messages is None:
            messages = [{"role": "system", "content": build_system_prompt()}]
            state.context["llm_messages"] = messages
        messages.append({"role": "user", "content": message})
        tool_defs = _openai_tools()

        for _ in range(MAX_TOOL_ITERATIONS):
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tool_defs,
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                return Reply((msg.content or "").strip() or "Could you rephrase that?")
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps({"error": "malformed tool arguments"})})
                    continue
                content, _ = agent.call_tool_json(tc.function.name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
        return Reply("I'm having trouble completing that — could you try rephrasing?")
