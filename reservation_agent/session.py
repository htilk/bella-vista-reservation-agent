"""Per-conversation state and the brain's reply type.

Kept in its own module so both brains (nlu.py / llm.py) and the agent can import
it without a circular dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Reply:
    text: str
    confirmation_code: Optional[str] = None  # set so the UI can render a badge


@dataclass
class ConversationState:
    history: list[dict[str, Any]] = field(default_factory=list)  # provider-agnostic turns
    flow: Optional[str] = None        # 'book' | 'cancel' | 'modify' | None
    awaiting: Optional[str] = None     # which field/confirmation we expect next
    pending: dict[str, Any] = field(default_factory=dict)   # in-progress booking fields
    context: dict[str, Any] = field(default_factory=dict)   # flow-specific scratch

    def reset_flow(self) -> None:
        self.flow = None
        self.awaiting = None
        self.pending = {}
        self.context = {}
