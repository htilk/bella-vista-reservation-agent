"""The Agent: one per chat session. Owns conversation state and the tool
dispatcher that BOTH brains call, so every tool invocation is logged once
(BRD §6.2) and domain errors are handled uniformly.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from . import llm, tools
from .errors import ReservationError
from .models import Reservation
from .nlu import DeterministicBrain
from .session import ConversationState, Reply
from .store import ReservationStore

log = logging.getLogger("bella_vista.agent")

CODE_BADGE_RE = re.compile(r"\bBV-[A-Z0-9]{4}\b")

_BRAIN = None  # brains are stateless (state lives in ConversationState) -> share one


def get_brain():
    """Pick the LLM brain if a key is configured, else the deterministic one."""
    global _BRAIN
    if _BRAIN is None:
        _BRAIN = llm.select_brain()
        if _BRAIN is not None:
            log.info("Conversation brain: LLM (%s)", _BRAIN.provider.model)
        else:
            _BRAIN = DeterministicBrain()
            log.info("Conversation brain: deterministic NLU (no API key found)")
    return _BRAIN


def _serialize(result: Any):
    if isinstance(result, Reservation):
        return result.to_dict()
    return result  # dict | None already JSON-friendly


class Agent:
    def __init__(self, store: ReservationStore, brain=None):
        self.store = store
        self.brain = brain or get_brain()
        self.state = ConversationState()

    def send(self, message: str) -> dict:
        """Process one guest message, return {reply, confirmation_code}."""
        self.state.history.append({"role": "user", "content": message})
        reply = self.brain.respond(self.state, message, self)
        self.state.history.append({"role": "assistant", "content": reply.text})
        code = reply.confirmation_code
        if not code:  # let any BV-XXXX in the text surface as a UI badge
            m = CODE_BADGE_RE.search(reply.text)
            code = m.group(0) if m else None
        return {"reply": reply.text, "confirmation_code": code}

    # ------------------------------------------------ tool dispatch (single log point)
    def call_tool(self, name: str, **kwargs):
        """Execute a tool, logging the invocation. Raises ReservationError as-is."""
        log.info("TOOL %s %s", name, json.dumps(kwargs, default=str))
        fn = tools.TOOL_FUNCS[name]
        try:
            result = fn(self.store, **kwargs)
        except ReservationError as exc:
            log.info("TOOL %s -> error: %s", name, exc.message)
            raise
        log.info("TOOL %s -> ok", name)
        return result

    def call_tool_json(self, name: str, args: dict) -> tuple[str, bool]:
        """LLM-facing wrapper: returns (json_text, is_error)."""
        try:
            result = self.call_tool(name, **args)
            return json.dumps(_serialize(result), default=str), False
        except ReservationError as exc:
            return json.dumps({"error": exc.message}), True
        except Exception as exc:  # malformed args from the model -> report, don't crash
            log.warning("TOOL %s raised %s", name, exc)
            return json.dumps({"error": f"Invalid tool input: {exc}"}), True
