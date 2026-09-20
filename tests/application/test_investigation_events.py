"""
Live investigation streaming — the events a watcher is allowed to see.

Two things are under test: that the trail is emitted in a sensible
order as the work happens, and that it never leaks the prompts or the
planner's deliberation. The stubs are the same shape as
test_investigate_question's.
"""
import asyncio
import unittest

from app.application.interfaces.code_parser import TraceStep
from app.application.interfaces.llm_client import LLMProviderError
from app.application.interfaces.vector_store import VectorQueryResult
from app.application.investigation_events import (
    InvestigationEvent,
    InvestigationEventType,
    emit,
)
from app.application.use_cases.investigate_question import DeepInvestigationUseCase
from app.domain.entities import (
    CodeChunk,
    FileNode,
    ParsedRepoIndex,
    ParsedSymbol,
    Question,
    RepoManifest,
)
from app.domain.value_objects import QuestionMode, RepoUrl

REPO = "acme/widgets"
SHA = "abc123"

SYSTEM_PROMPT_MARKER = "You are planning a code investigation"


def chunk(path, start=1, end=5):
    return CodeChunk(
        id=f"{path}:{start}", file_path=path, content=f"# code in {path}",
        start_line=start, end_line=end,
    )


def hit(path, sim):
    return VectorQueryResult(chunk=chunk(path), similarity=sim)


class ScriptedLLM:
    def __init__(self, replies):
        self.embedded = []
        self.replies = list(replies)
        self.prompts = []

    async def embed(self, texts):
        self.embedded.extend(texts)
        return [[float(len(self.embedded) - 1)] for _ in texts]

    async def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else "SYNTHESIZED ANSWER"


class FailingSynthesisLLM(ScriptedLLM):
    async def generate(self, prompt, **kwargs):
        raise LLMProviderError("stub", "capacity")


class ScriptedVectors:
    def __init__(self, result_sets):
        self.result_sets = result_sets

    async def query(self, *, repo_full_name, commit_sha, query_embedding, top_k):
        index = int(query_embedding[0])
        return self.result_sets[index] if index < len(self.result_sets) else []


class NoTraceParser:
    def trace_calls(self, **kwargs):
        return []


class TracingParser:
    def trace_calls(self, *, entry_file, entry_symbol, parsed_index, max_hops):
        return [
            TraceStep(file_path="core/db.py", symbol_name="save", start_line=1, end_line=4,
                      snippet="save()")
        ]


class StaticCache:
    def __init__(self, paths, symbols=()):
        self.manifest = RepoManifest(
            repo_url=RepoUrl(f"https://github.com/{REPO}"), commit_sha=SHA,
            files=[FileNode(path=p, size_bytes=10, content="x") for p in paths]
            + [FileNode(path="core/db.py", size_bytes=10, content="x\n" * 5)],
        )
        self.index = ParsedRepoIndex(
            repo_full_name=REPO, commit_sha=SHA, symbols=list(symbols), module_edges=[]
        )

    async def get(self, repo_full_name, commit_sha):
        return self.manifest

    async def get_parsed_index(self, repo_full_name, commit_sha):
        return self.index


def investigate(replies, result_sets, *, parser=None, symbols=(), llm_cls=ScriptedLLM, max_steps=5):
    """Runs an investigation with a recording sink; returns (answer, events, llm)."""
    llm = llm_cls(replies)
    events = []

    async def sink(event):
        events.append(event)

    paths = sorted({r.chunk.file_path for rs in result_sets for r in rs}) or ["a.py"]
    use_case = DeepInvestigationUseCase(
        llm_client=llm,
        vector_store=ScriptedVectors(result_sets),
        code_parser=parser or NoTraceParser(),
        cache=StaticCache(paths, symbols),
        max_steps=max_steps,
    )
    answer = asyncio.run(
        use_case.execute(
            question=Question(text="how does auth work?", mode=QuestionMode.DEEP),
            repo_full_name=REPO,
            commit_sha=SHA,
            on_event=sink,
        )
    )
    return answer, events, llm


def types_of(events):
    return [e.type.value for e in events]


class EventTrailTest(unittest.TestCase):
    def test_a_successful_investigation_opens_and_closes_correctly(self):
        _answer, events, _llm = investigate(
            ['{"sufficient": true, "missing": "", "next_search": ""}'],
            [[hit("auth/login.py", 0.9)]],
        )
        kinds = types_of(events)
        self.assertEqual(kinds[0], "investigation_started")
        self.assertEqual(kinds[-1], "completed")
        for expected in ("search_started", "search_result", "source_inspection", "answer_generation"):
            self.assertIn(expected, kinds)

    def test_search_events_carry_the_phrase_and_the_hit_count(self):
        _answer, events, _llm = investigate(
            ['{"sufficient": true, "missing": "", "next_search": ""}'],
            [[hit("auth/login.py", 0.9), hit("auth/session.py", 0.8)]],
        )
        started = next(e for e in events if e.type.value == "search_started")
        result = next(e for e in events if e.type.value == "search_result")

        self.assertEqual(started.metadata["query"], "how does auth work?")
        self.assertEqual(result.metadata["count"], 2)
        self.assertEqual(result.metadata["files"], ["auth/login.py", "auth/session.py"])

    def test_graph_events_are_emitted_when_a_trace_runs(self):
        symbols = [
            ParsedSymbol(name="login", kind="function", file_path="auth/login.py",
                         start_line=1, end_line=5)
        ]
        _answer, events, _llm = investigate(
            ['{"sufficient": true, "missing": "", "next_search": ""}'],
            [[hit("auth/login.py", 0.9)]],
            parser=TracingParser(),
            symbols=symbols,
        )
        kinds = types_of(events)
        self.assertIn("graph_traversal_started", kinds)

        traversal = next(e for e in events if e.type.value == "graph_traversal_started")
        result = next(e for e in events if e.type.value == "graph_result")
        self.assertEqual(traversal.metadata["symbol"], "login")
        self.assertEqual(result.metadata["count"], 1)

    def test_each_extra_round_emits_its_own_search_pair(self):
        _answer, events, _llm = investigate(
            [
                '{"sufficient": false, "missing": "token check", "next_search": "token"}',
                '{"sufficient": true, "missing": "", "next_search": ""}',
            ],
            [[hit("auth/login.py", 0.9)], [hit("auth/token.py", 0.7)]],
        )
        self.assertEqual(types_of(events).count("search_started"), 2)
        phrases = [e.metadata["query"] for e in events if e.type.value == "search_started"]
        self.assertEqual(phrases[1], "token")

    def test_completed_event_reports_the_real_citation_count(self):
        answer, events, _llm = investigate(
            ['{"sufficient": true, "missing": "", "next_search": ""}'],
            [[hit("auth/login.py", 0.9)]],
        )
        completed = next(e for e in events if e.type.value == "completed")
        self.assertEqual(completed.metadata["citations"], len(answer.citations))

    def test_a_repo_with_no_matches_still_completes(self):
        _answer, events, _llm = investigate([], [[]])
        kinds = types_of(events)
        self.assertEqual(kinds[-1], "completed")
        self.assertNotIn("answer_generation", kinds)

    def test_synthesis_failure_emits_a_recoverable_error_then_completes(self):
        _answer, events, _llm = investigate(
            [], [[hit("auth/login.py", 0.9)]], llm_cls=FailingSynthesisLLM
        )
        error = next(e for e in events if e.type.value == "error")
        self.assertTrue(error.metadata["recoverable"])
        self.assertEqual(types_of(events)[-1], "completed")


class NoReasoningLeakTest(unittest.TestCase):
    """The streamed trail must describe actions, never model deliberation."""

    def _all_text(self, events):
        return " ".join(
            [e.message for e in events] + [str(e.metadata) for e in events]
        )

    def test_prompts_and_system_instructions_never_appear_in_events(self):
        _answer, events, llm = investigate(
            [
                '{"sufficient": false, "missing": "how tokens are checked internally",'
                ' "next_search": "token"}',
                '{"sufficient": true, "missing": "", "next_search": ""}',
            ],
            [[hit("auth/login.py", 0.9)], [hit("auth/token.py", 0.7)]],
        )
        text = self._all_text(events)

        self.assertNotIn(SYSTEM_PROMPT_MARKER, text)
        for prompt in llm.prompts:
            self.assertNotIn(prompt, text)
        # The planner's private "what is still missing" note stays private.
        self.assertNotIn("how tokens are checked internally", text)

    def test_raw_code_content_is_not_streamed(self):
        _answer, events, _llm = investigate(
            ['{"sufficient": true, "missing": "", "next_search": ""}'],
            [[hit("auth/login.py", 0.9)]],
        )
        self.assertNotIn("# code in auth/login.py", self._all_text(events))

    def test_every_emitted_type_is_in_the_public_vocabulary(self):
        _answer, events, _llm = investigate(
            ['{"sufficient": true, "missing": "", "next_search": ""}'],
            [[hit("auth/login.py", 0.9)]],
        )
        allowed = {t.value for t in InvestigationEventType}
        self.assertTrue(set(types_of(events)).issubset(allowed))


class EmitterTest(unittest.TestCase):
    def test_emitting_to_no_sink_is_a_no_op(self):
        asyncio.run(emit(None, InvestigationEventType.COMPLETED, "done"))

    def test_a_broken_sink_never_breaks_the_investigation(self):
        async def broken(event):
            raise RuntimeError("listener exploded")

        asyncio.run(emit(broken, InvestigationEventType.COMPLETED, "done"))

    def test_investigation_finishes_even_when_the_sink_keeps_failing(self):
        llm = ScriptedLLM(['{"sufficient": true, "missing": "", "next_search": ""}'])

        async def broken(event):
            raise RuntimeError("listener exploded")

        use_case = DeepInvestigationUseCase(
            llm_client=llm,
            vector_store=ScriptedVectors([[hit("auth/login.py", 0.9)]]),
            code_parser=NoTraceParser(),
            cache=StaticCache(["auth/login.py"]),
            max_steps=3,
        )
        answer = asyncio.run(
            use_case.execute(
                question=Question(text="q", mode=QuestionMode.DEEP),
                repo_full_name=REPO,
                commit_sha=SHA,
                on_event=broken,
            )
        )
        self.assertTrue(answer.citations)

    def test_event_serializes_to_the_documented_shape(self):
        event = InvestigationEvent(
            type=InvestigationEventType.SEARCH_RESULT,
            message="Found 4 references to check_spam_flags",
            metadata={"symbol": "check_spam_flags", "count": 4},
        )
        self.assertEqual(
            event.to_dict(),
            {
                "type": "search_result",
                "message": "Found 4 references to check_spam_flags",
                "metadata": {"symbol": "check_spam_flags", "count": 4},
            },
        )


class BackwardCompatibilityTest(unittest.TestCase):
    def test_investigation_without_a_sink_behaves_exactly_as_before(self):
        llm = ScriptedLLM(['{"sufficient": true, "missing": "", "next_search": ""}'])
        use_case = DeepInvestigationUseCase(
            llm_client=llm,
            vector_store=ScriptedVectors([[hit("auth/login.py", 0.9)]]),
            code_parser=NoTraceParser(),
            cache=StaticCache(["auth/login.py"]),
            max_steps=3,
        )
        answer = asyncio.run(
            use_case.execute(
                question=Question(text="q", mode=QuestionMode.DEEP),
                repo_full_name=REPO,
                commit_sha=SHA,
            )
        )
        self.assertTrue(answer.investigation_log)
        self.assertTrue(answer.citations)


if __name__ == "__main__":
    unittest.main()
