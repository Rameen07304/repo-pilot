"""
"How does login work" -> a flow diagram.

The design claim is narrow and worth testing precisely: a model chooses
WHERE to start, and the parse decides WHAT HAPPENS. So the tests here are
almost all about the boundary — that the model cannot name a function
that was not on the shortlist, and that every way the model can fail
leaves the feature working.
"""
import unittest

from app.application.interfaces.llm_client import LLMProviderError
from app.application.interfaces.vector_store import VectorQueryResult
from app.application.use_cases.trace_call_sequence import TraceCallSequenceUseCase
from app.application.use_cases.trace_flow_for_question import (
    NoFlowFoundError,
    TraceFlowForQuestionUseCase,
)
from app.domain.entities import CodeChunk, ParsedRepoIndex, ParsedSymbol

REPO, SHA = "acme/widgets", "abc123"


def sym(name, file, start=1, sites=()):
    return ParsedSymbol(
        name=name, kind="function", file_path=file, start_line=start, end_line=start + 4,
        calls=[n for n, _ in sites], call_sites=list(sites),
    )


SYMBOLS = [
    sym("login_view", "account/views.py", 10, [("authenticate", 12)]),
    sym("authenticate", "account/auth.py", 3, [("lookup", 5)]),
    sym("lookup", "account/db.py", 8),
    sym("ticket_list", "tickets/views.py", 4, [("fetch", 6)]),
    sym("fetch", "tickets/db.py", 2),
]


class FakeCache:
    def __init__(self, index):
        self._index = index

    async def get_parsed_index(self, *a):
        return self._index

    async def get(self, *a):
        return None


class FakeVectors:
    def __init__(self, hits):
        self._hits = hits
        self.queried_with = None

    async def query(self, *, repo_full_name, commit_sha, query_embedding, top_k):
        self.queried_with = query_embedding
        return [
            VectorQueryResult(
                chunk=CodeChunk(id=f"c{i}", file_path=path, content="", start_line=1, end_line=2),
                similarity=score,
            )
            for i, (path, score) in enumerate(self._hits)
        ]


class FakeLLM:
    def __init__(self, reply=None, *, raises=None):
        self._reply = reply
        self._raises = raises
        self.prompts = []

    async def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def generate(self, prompt, *, system=None, max_tokens=None, temperature=None):
        self.prompts.append(prompt)
        if self._raises:
            raise self._raises
        return self._reply


def build(hits, llm, *, symbols=SYMBOLS, use_llm_choice=True):
    index = ParsedRepoIndex(repo_full_name=REPO, commit_sha=SHA, symbols=list(symbols))
    cache = FakeCache(index)
    return TraceFlowForQuestionUseCase(
        vector_store=FakeVectors(hits),
        llm_client=llm,
        sequence_use_case=TraceCallSequenceUseCase(cache=cache),
        use_llm_choice=use_llm_choice,
    )


def run(uc, question="how does login work"):
    import asyncio

    return asyncio.run(
        uc.execute(repo_full_name=REPO, commit_sha=SHA, question=question)
    )


HITS = [("account/views.py", 0.9), ("account/auth.py", 0.6), ("tickets/views.py", 0.2)]


class FlowTest(unittest.TestCase):
    def test_the_model_picks_the_entry_and_the_parse_draws_the_rest(self):
        flow = run(build(HITS, FakeLLM('{"index": 0, "reason": "the login view"}')))
        self.assertEqual(flow.entry_symbol, "login_view")
        self.assertEqual(flow.chosen_by, "model")
        self.assertEqual(flow.reason, "the login view")
        # The trace itself is parse-driven and goes further than the
        # shortlist ever mentioned.
        self.assertEqual(
            [s.to_symbol for s in flow.sequence.steps], ["authenticate", "lookup"]
        )

    def test_the_question_reaches_the_search_and_the_prompt(self):
        llm = FakeLLM('{"index": 0, "reason": "x"}')
        run(build(HITS, llm), question="how does login work")
        self.assertIn("how does login work", llm.prompts[0])

    def test_only_files_search_found_can_supply_a_starting_point(self):
        # tickets/views.py is not in the hits, so ticket_list must not be
        # offered however traceable it is.
        flow = run(build([("account/views.py", 0.9)], FakeLLM('{"index": 0, "reason": "x"}')))
        names = {c.symbol for c in flow.candidates}
        self.assertEqual(names, {"login_view"})

    def test_an_index_outside_the_shortlist_falls_back_instead_of_being_used(self):
        # This is the guard that matters: a model naming something that
        # was not offered is exactly what this design exists to stop.
        flow = run(build(HITS, FakeLLM('{"index": 99, "reason": "invented"}')))
        self.assertEqual(flow.chosen_by, "retrieval")
        self.assertEqual(flow.entry_symbol, "login_view")  # top by similarity
        self.assertNotIn("invented", flow.reason)

    def test_a_negative_index_is_not_treated_as_python_indexing(self):
        # -1 would silently select the LAST candidate.
        flow = run(build(HITS, FakeLLM('{"index": -1, "reason": "x"}')))
        self.assertEqual(flow.chosen_by, "retrieval")

    def test_unparsable_output_falls_back(self):
        flow = run(build(HITS, FakeLLM("I think you should look at login_view!")))
        self.assertEqual(flow.chosen_by, "retrieval")
        self.assertEqual(flow.entry_symbol, "login_view")

    def test_a_provider_failure_still_produces_a_diagram(self):
        flow = run(build(HITS, FakeLLM(raises=LLMProviderError("test", "down"))))
        self.assertEqual(flow.chosen_by, "retrieval")
        self.assertTrue(flow.sequence.steps)

    def test_a_fenced_json_reply_is_still_read(self):
        flow = run(build(HITS, FakeLLM('```json\n{"index": 0, "reason": "ok"}\n```')))
        self.assertEqual(flow.chosen_by, "model")

    def test_the_model_is_skipped_when_there_is_nothing_to_choose(self):
        llm = FakeLLM('{"index": 0, "reason": "x"}')
        run(build([("account/views.py", 0.9)], llm))
        self.assertEqual(llm.prompts, [], "one candidate needs no model call")

    def test_it_can_be_run_with_the_model_switched_off_entirely(self):
        llm = FakeLLM('{"index": 1, "reason": "x"}')
        flow = run(build(HITS, llm, use_llm_choice=False))
        self.assertEqual(flow.chosen_by, "retrieval")
        self.assertEqual(llm.prompts, [])

    def test_candidates_are_ranked_by_relevance_first(self):
        flow = run(build(HITS, FakeLLM(raises=LLMProviderError("test", "down"))))
        self.assertEqual(flow.candidates[0].symbol, "login_view")
        self.assertGreaterEqual(
            flow.candidates[0].similarity, flow.candidates[-1].similarity
        )

    def test_alternatives_exclude_the_one_that_was_chosen(self):
        flow = run(build(HITS, FakeLLM('{"index": 0, "reason": "x"}')))
        self.assertNotIn(
            (flow.entry_file, flow.entry_symbol),
            [(a.file_path, a.symbol) for a in flow.alternatives],
        )

    def test_nothing_relevant_is_a_clear_refusal_not_an_empty_diagram(self):
        uc = build([], FakeLLM('{"index": 0, "reason": "x"}'))
        with self.assertRaises(NoFlowFoundError):
            run(uc)

    def test_a_relevant_file_with_nothing_traceable_is_not_a_starting_point(self):
        # The file matches, but nothing in it calls anything internal, so
        # starting there would draw an empty diagram.
        uc = build([("account/db.py", 0.95)], FakeLLM('{"index": 0, "reason": "x"}'))
        with self.assertRaises(NoFlowFoundError):
            run(uc)

    def test_an_empty_question_is_refused(self):
        uc = build(HITS, FakeLLM('{"index": 0, "reason": "x"}'))
        with self.assertRaises(NoFlowFoundError):
            run(uc, question="   ")

    def test_an_embedding_failure_says_so_rather_than_guessing(self):
        class NoEmbed(FakeLLM):
            async def embed(self, texts):
                raise LLMProviderError("test", "embeddings down")

        with self.assertRaises(NoFlowFoundError):
            run(build(HITS, NoEmbed('{"index": 0, "reason": "x"}')))

    def test_a_long_reason_is_truncated_not_trusted_wholesale(self):
        flow = run(build(HITS, FakeLLM('{"index": 0, "reason": "' + "x" * 900 + '"}')))
        self.assertLessEqual(len(flow.reason), 240)


if __name__ == "__main__":
    unittest.main()
