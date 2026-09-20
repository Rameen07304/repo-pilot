"""
The vocabulary of a live investigation.

These events are what the user is allowed to watch: what the system
DID, never why a model thought it should. Concretely, nothing here
carries a prompt, a system message, or any model deliberation — the
planner's chosen search phrase appears because it is an action we then
performed (we embedded and searched for it) and it is already shown in
the finished investigation log, not because it is reasoning.

Emitting is fire-and-forget: a use case calls the emitter, and a
failure to deliver an event must never fail the investigation, since
the answer itself does not depend on anyone watching.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


class InvestigationEventType(str, Enum):
    INVESTIGATION_STARTED = "investigation_started"
    SEARCH_STARTED = "search_started"
    SEARCH_RESULT = "search_result"
    GRAPH_TRAVERSAL_STARTED = "graph_traversal_started"
    GRAPH_RESULT = "graph_result"
    SOURCE_INSPECTION = "source_inspection"
    ANSWER_GENERATION = "answer_generation"
    VERIFICATION = "verification"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class InvestigationEvent:
    type: InvestigationEventType
    message: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type.value, "message": self.message, "metadata": self.metadata}


class InvestigationEventSink(Protocol):
    async def __call__(self, event: InvestigationEvent) -> None:
        ...


async def emit(
    sink: Callable[[InvestigationEvent], Awaitable[None]] | None,
    event_type: InvestigationEventType,
    message: str,
    **metadata,
) -> None:
    """
    Deliver one event, swallowing delivery failures. A disconnected
    browser is the normal case, not an error worth aborting an
    in-flight investigation over.
    """
    if sink is None:
        return
    try:
        await sink(InvestigationEvent(type=event_type, message=message, metadata=metadata))
    except Exception:  # noqa: BLE001 — a broken listener must not break the investigation
        logger.debug("Investigation event sink raised; continuing", exc_info=True)
