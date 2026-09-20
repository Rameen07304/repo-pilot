"""
GET /api/ask/stream — the SSE framing around a deep investigation.

The use case is stubbed so these tests are about the transport: that
events arrive as well-formed SSE in order, that the final answer
payload matches what POST /api/ask returns, and that failures become
an error event rather than a half-written stream.
"""
import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.investigation_events import (
    InvestigationEvent,
    InvestigationEventType as EventType,
)
from app.application.use_cases.answer_question import RepoNotIngestedError
from app.domain.entities import Answer, Question
from app.domain.value_objects import Citation, ConfidenceScore, QuestionMode
from app.infrastructure.llm.quota_aware_llm_client import DailyQuotaExceededError
from app.infrastructure.security.rate_limiter import FixedWindowRateLimiter
from app.interface.routes import investigate_stream

REPO = "acme/app"
SHA = "sha1"


class StubDeepUseCase:
    """Emits a scripted trail, then returns a fixed Answer (or raises)."""

    def __init__(self, *, events=None, raises=None):
        self._events = events or [
            (EventType.INVESTIGATION_STARTED, "Investigating: how does auth work?", {}),
            (EventType.SEARCH_STARTED, 'Searching for "auth"...', {"query": "auth"}),
            (EventType.SEARCH_RESULT, "Found 2 new location(s) in 1 file(s)", {"count": 2}),
            (EventType.COMPLETED, "Investigation complete", {"citations": 1, "steps": 1}),
        ]
        self._raises = raises
        self.received_question = None

    async def execute(self, *, question, repo_full_name, commit_sha, on_event=None):
        self.received_question = question
        for event_type, message, metadata in self._events:
            if on_event is not None:
                await on_event(
                    InvestigationEvent(type=event_type, message=message, metadata=metadata)
                )
            await asyncio.sleep(0)  # let the drain loop run between events
        if self._raises is not None:
            raise self._raises
        return Answer(
            question=question,
            text="Auth works like this.",
            citations=[Citation(file_path="auth/login.py", start_line=1, end_line=5)],
            confidence=ConfidenceScore(0.82),
            mode_used=QuestionMode.DEEP,
            steps_taken=1,
            investigation_log=["step 1: searched \"auth\" -> 2 new location(s)"],
        )


class Container:
    def __init__(self, use_case, *, limit=100):
        self.trust_proxy_headers = False
        self.deep_question_limiter = FixedWindowRateLimiter(max_events=limit, window_seconds=3600)
        self.deep_ask_use_case = use_case


def client(use_case, *, limit=100):
    app = FastAPI()
    app.include_router(investigate_stream.router)
    app.state.container = Container(use_case, limit=limit)
    return TestClient(app)


def stream(c, question="how does auth work?"):
    params = {"repo_full_name": REPO, "commit_sha": SHA, "question": question}
    with c.stream("GET", "/api/ask/stream", params=params) as res:
        return res.status_code, "".join(res.iter_text())


def parse_sse(text):
    """Returns [(event_name, data_json_string)], ignoring comment/keep-alive lines."""
    blocks = []
    for raw in text.split("\n\n"):
        name = data = None
        for line in raw.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = line[len("data: "):]
        if name is not None and data is not None:
            blocks.append((name, data))
    return blocks


class StreamFramingTest(unittest.TestCase):
    def test_events_arrive_in_order_and_end_with_the_answer(self):
        status, body = stream(client(StubDeepUseCase()))
        self.assertEqual(status, 200)

        names = [name for name, _ in parse_sse(body)]
        self.assertEqual(
            names,
            ["investigation_started", "search_started", "search_result", "completed", "answer"],
        )

    def test_content_type_is_event_stream_and_buffering_is_disabled(self):
        params = {"repo_full_name": REPO, "commit_sha": SHA, "question": "q"}
        with client(StubDeepUseCase()).stream("GET", "/api/ask/stream", params=params) as res:
            self.assertIn("text/event-stream", res.headers["content-type"])
            self.assertEqual(res.headers["x-accel-buffering"], "no")
            self.assertEqual(res.headers["cache-control"], "no-cache")
            res.read()

    def test_each_event_carries_type_message_and_metadata(self):
        import json

        _status, body = stream(client(StubDeepUseCase()))
        name, data = parse_sse(body)[1]
        payload = json.loads(data)

        self.assertEqual(name, "search_started")
        self.assertEqual(payload["type"], "search_started")
        self.assertTrue(payload["message"])
        self.assertEqual(payload["metadata"]["query"], "auth")

    def test_answer_payload_matches_the_non_streaming_response_shape(self):
        import json

        _status, body = stream(client(StubDeepUseCase()))
        _name, data = parse_sse(body)[-1]
        payload = json.loads(data)

        self.assertEqual(
            sorted(payload),
            ["answer_text", "citations", "confidence", "investigation_log",
             "mode_used", "steps_taken"],
        )
        self.assertEqual(payload["citations"], ["auth/login.py:1-5"])
        self.assertEqual(payload["mode_used"], "deep")

    def test_the_question_reaches_the_use_case_in_deep_mode(self):
        use_case = StubDeepUseCase()
        stream(client(use_case), question="where is login handled?")
        self.assertIsInstance(use_case.received_question, Question)
        self.assertEqual(use_case.received_question.text, "where is login handled?")
        self.assertEqual(use_case.received_question.mode, QuestionMode.DEEP)

    def test_a_message_containing_newlines_cannot_break_the_framing(self):
        events = [
            (EventType.SEARCH_RESULT, "line one\nline two\n\nevent: injected", {}),
            (EventType.COMPLETED, "done", {}),
        ]
        _status, body = stream(client(StubDeepUseCase(events=events)))
        names = [name for name, _ in parse_sse(body)]
        self.assertEqual(names, ["search_result", "completed", "answer"])
        self.assertNotIn("injected", names)


class StreamErrorTest(unittest.TestCase):
    def test_uningested_repo_becomes_an_error_event(self):
        import json

        use_case = StubDeepUseCase(raises=RepoNotIngestedError("not ingested"))
        status, body = stream(client(use_case))

        self.assertEqual(status, 200)  # the stream opened before the failure was known
        name, data = parse_sse(body)[-1]
        self.assertEqual(name, "error")
        self.assertEqual(json.loads(data)["metadata"]["status"], 404)

    def test_quota_exhaustion_becomes_a_503_error_event(self):
        import json

        use_case = StubDeepUseCase(raises=DailyQuotaExceededError("out of quota"))
        _status, body = stream(client(use_case))
        _name, data = parse_sse(body)[-1]
        self.assertEqual(json.loads(data)["metadata"]["status"], 503)

    def test_unexpected_failure_becomes_a_generic_error_event(self):
        import json

        use_case = StubDeepUseCase(raises=RuntimeError("boom"))
        _status, body = stream(client(use_case))
        name, data = parse_sse(body)[-1]
        payload = json.loads(data)

        self.assertEqual(name, "error")
        self.assertEqual(payload["metadata"]["status"], 500)
        self.assertNotIn("boom", payload["message"])  # internals are not leaked

    def test_no_answer_event_follows_a_failure(self):
        use_case = StubDeepUseCase(raises=RuntimeError("boom"))
        _status, body = stream(client(use_case))
        self.assertNotIn("answer", [name for name, _ in parse_sse(body)])

    def test_rate_limit_is_rejected_before_the_stream_opens(self):
        c = client(StubDeepUseCase(), limit=1)
        self.assertEqual(stream(c)[0], 200)
        status, body = stream(c)
        self.assertEqual(status, 429)
        self.assertNotIn("event:", body)

    def test_an_empty_question_is_rejected_by_validation(self):
        params = {"repo_full_name": REPO, "commit_sha": SHA, "question": ""}
        res = client(StubDeepUseCase()).get("/api/ask/stream", params=params)
        self.assertEqual(res.status_code, 422)


if __name__ == "__main__":
    unittest.main()
