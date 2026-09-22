"""
GET /api/ask/stream — the same deep investigation as POST /api/ask,
streamed as it happens.

SSE, not WebSockets: this is strictly one-way server-to-client progress
over plain HTTP, which is exactly what SSE is for, and it needs no new
protocol handling anywhere in the stack.

Shape: the investigation runs as a task that pushes events into a
queue; this handler drains the queue into the response and finishes
with one `answer` event carrying the same payload POST /api/ask
returns. Running it as a task (rather than yielding from inside the
use case) is what lets the queue keep draining while a slow LLM call
is in flight — otherwise every event would arrive in one burst at the
end, which defeats the point.

GET, not POST, because EventSource can only issue GETs; parameters
therefore arrive as query params, validated by the same Pydantic-typed
signature FastAPI applies to a body.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.application.investigation_events import (
    InvestigationEvent,
    InvestigationEventType as EventType,
)
from app.application.use_cases.answer_question import RepoNotIngestedError
from app.domain.entities import ConversationTurn, Question
from app.domain.value_objects import Persona, QuestionMode
from app.infrastructure.llm.quota_aware_llm_client import DailyQuotaExceededError
from app.interface.client_ip import get_client_ip

logger = logging.getLogger(__name__)
router = APIRouter()

# Sent when nothing has happened for a while, so proxies that time out
# idle connections keep this one open. A comment line is valid SSE the
# EventSource API ignores entirely.
_HEARTBEAT_SECONDS = 15.0


def _sse(event: str, data: dict) -> str:
    # json.dumps never emits a raw newline inside a string, so the
    # payload cannot break out of the one-line `data:` framing.
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _parse_history(raw: str, *, max_turns: int = 10) -> list[ConversationTurn]:
    """
    Decode the history query parameter. Anything unexpected yields an
    empty history rather than an error — a follow-up without history is
    still a perfectly answerable question.
    """
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        logger.warning("Ignoring malformed history parameter on /api/ask/stream")
        return []
    if not isinstance(data, list):
        return []

    turns: list[ConversationTurn] = []
    for item in data[-max_turns:]:
        if not isinstance(item, dict) or not isinstance(item.get("question"), str):
            continue
        cited = item.get("cited_files")
        turns.append(ConversationTurn(
            question=item["question"][:500],
            answer=item["answer"][:4000] if isinstance(item.get("answer"), str) else "",
            cited_files=[c[:500] for c in cited[:20] if isinstance(c, str)] if isinstance(cited, list) else [],
        ))
    return turns


@router.get("/api/ask/stream")
async def stream_investigation(
    request: Request,
    repo_full_name: str = Query(..., max_length=200),
    commit_sha: str = Query(..., max_length=100),
    question: str = Query(..., min_length=1, max_length=500),
    persona: str = Query(default="newcomer", pattern="^(newcomer|senior|product|learner)$"),
    # EventSource can only issue a GET, so the conversation history
    # arrives as one JSON-encoded parameter rather than a body. It is
    # parsed defensively and silently dropped if malformed: history is
    # an aid to understanding the question, never required to answer it.
    history: str = Query(default="", max_length=8000),
) -> StreamingResponse:
    container = request.app.state.container
    client_ip = get_client_ip(request, trust_proxy_headers=container.trust_proxy_headers)

    # Rate limit BEFORE opening the stream — a rejected request costs
    # zero LLM calls, same rule as POST /api/ask.
    allowed, _remaining = container.deep_question_limiter.check_and_increment(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Deep investigation rate limit reached for your IP this hour. "
                   "Try again later, or use quick mode.",
        )

    try:
        parsed_question = Question(
            text=question,
            mode=QuestionMode.DEEP,
            persona=Persona(persona),
            history=_parse_history(history),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    queue: asyncio.Queue[InvestigationEvent | None] = asyncio.Queue()

    async def on_event(event: InvestigationEvent) -> None:
        await queue.put(event)

    async def run() -> dict:
        try:
            answer = await container.deep_ask_use_case.execute(
                question=parsed_question,
                repo_full_name=repo_full_name,
                commit_sha=commit_sha,
                on_event=on_event,
            )
            return {
                "answer_text": answer.text,
                "citations": [str(c) for c in answer.citations],
                "confidence": answer.confidence.value,
                "mode_used": answer.mode_used.value,
                "steps_taken": answer.steps_taken,
                "investigation_log": answer.investigation_log,
            }
        finally:
            # Always wakes the drain loop, including on failure.
            await queue.put(None)

    async def event_stream():
        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield _sse(event.type.value, event.to_dict())

            try:
                payload = await task
            except RepoNotIngestedError as e:
                yield _sse(EventType.ERROR.value, {"type": EventType.ERROR.value, "message": str(e), "metadata": {"status": 404}})
                return
            except DailyQuotaExceededError as e:
                yield _sse(EventType.ERROR.value, {"type": EventType.ERROR.value, "message": str(e), "metadata": {"status": 503}})
                return
            except Exception:
                logger.exception("Streamed investigation failed")
                yield _sse(EventType.ERROR.value, {
                    "type": EventType.ERROR.value,
                    "message": "The investigation failed unexpectedly.",
                    "metadata": {"status": 500},
                })
                return

            yield _sse("answer", payload)
        finally:
            # A disconnected browser cancels this generator; the
            # investigation behind it should not keep burning LLM calls
            # for a reader who has gone away.
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx would otherwise buffer the whole stream
        },
    )
