"""
Conversation memory: a follow-up question is resolved against earlier
turns for retrieval and in the prompt, while citations and evidence
still come only from this request's own search.
"""
import asyncio
import unittest

from app.application.interfaces.vector_store import VectorQueryResult
from app.application.use_cases.answer_question import AnswerQuestionUseCase
from app.domain.entities import CodeChunk, ConversationTurn, FileNode, ParsedRepoIndex, Question, RepoManifest
from app.domain.value_objects import RepoUrl

REPO = "acme/widgets"
SHA = "abc123"


class RecordingLLM:
    def __init__(self):
        self.embedded: list[str] = []
        self.prompts: list[tuple[str, str]] = []

    async def embed(self, texts):
        self.embedded.extend(texts)
        return [[1.0] for _ in texts]

    async def generate(self, prompt, *, system=None, **kwargs):
        self.prompts.append((system or "", prompt))
        return "answer"


class OneHitVectors:
    async def query(self, **kwargs):
        chunk = CodeChunk(id="c1", file_path="app/auth.py", content="def login(): pass", start_line=1, end_line=1)
        return [VectorQueryResult(chunk=chunk, similarity=0.9)]


class NoTraceParser:
    def trace_calls(self, **kwargs):
        return []


class StaticCache:
    def __init__(self):
        self.manifest = RepoManifest(
            repo_url=RepoUrl(f"https://github.com/{REPO}"), commit_sha=SHA,
            files=[FileNode(path="app/auth.py", size_bytes=10, content="def login(): pass")],
        )
        self.index = ParsedRepoIndex(repo_full_name=REPO, commit_sha=SHA, symbols=[], module_edges=[])

    async def get(self, *_):
        return self.manifest

    async def get_parsed_index(self, *_):
        return self.index


def ask(question):
    llm = RecordingLLM()
    uc = AnswerQuestionUseCase(llm_client=llm, vector_store=OneHitVectors(), code_parser=NoTraceParser(), cache=StaticCache())
    answer = asyncio.run(uc.execute(question=question, repo_full_name=REPO, commit_sha=SHA))
    return answer, llm


class RetrievalTextTest(unittest.TestCase):
    def test_first_question_is_used_verbatim(self):
        q = Question(text="how does login work?")
        self.assertEqual(q.retrieval_text(), "how does login work?")
        self.assertFalse(q.is_follow_up)

    def test_follow_up_carries_previous_question_and_files(self):
        q = Question(
            text="and where is that validated?",
            history=[ConversationTurn(question="how does login work?", answer="It checks a token.", cited_files=["app/auth.py", "app/deps.py"])],
        )
        text = q.retrieval_text()
        self.assertIn("how does login work?", text)
        self.assertIn("app/auth.py", text)
        self.assertTrue(text.endswith("and where is that validated?"))  # user's words dominate
        self.assertTrue(q.is_follow_up)

    def test_only_the_most_recent_turn_steers_retrieval(self):
        q = Question(
            text="what about errors?",
            history=[
                ConversationTurn(question="how does routing work?", cited_files=["app/router.py"]),
                ConversationTurn(question="how does login work?", cited_files=["app/auth.py"]),
            ],
        )
        text = q.retrieval_text()
        self.assertIn("app/auth.py", text)
        self.assertNotIn("app/router.py", text)  # older turns stay out of the embedding


class PromptTest(unittest.TestCase):
    def test_history_absent_from_prompt_for_a_first_question(self):
        _, llm = ask(Question(text="how does login work?"))
        system, prompt = llm.prompts[0]
        self.assertNotIn("EARLIER IN THIS CONVERSATION", prompt)
        self.assertNotIn("follow-up", system)
        self.assertEqual(llm.embedded, ["how does login work?"])

    def test_history_appears_once_in_the_prompt_and_steers_embedding(self):
        q = Question(
            text="and where is that validated?",
            history=[ConversationTurn(question="how does login work?", answer="It checks a bearer token.", cited_files=["app/auth.py"])],
        )
        _, llm = ask(q)
        system, prompt = llm.prompts[0]
        self.assertIn("--- EARLIER IN THIS CONVERSATION", prompt)
        self.assertIn("Q: how does login work?", prompt)
        self.assertIn("A: It checks a bearer token.", prompt)
        self.assertIn("files cited: app/auth.py", prompt)
        self.assertIn("follow-up", system)  # told to use history only to resolve references
        self.assertIn("app/auth.py", llm.embedded[0])
        # the question itself still leads the prompt body
        self.assertIn("Question: and where is that validated?", prompt)

    def test_history_is_trimmed_to_the_configured_window(self):
        turns = [ConversationTurn(question=f"q{i}", answer=f"a{i}") for i in range(6)]
        llm = RecordingLLM()
        uc = AnswerQuestionUseCase(
            llm_client=llm, vector_store=OneHitVectors(), code_parser=NoTraceParser(),
            cache=StaticCache(), max_history_turns=2,
        )
        asyncio.run(uc.execute(question=Question(text="latest?", history=turns), repo_full_name=REPO, commit_sha=SHA))
        _, prompt = llm.prompts[0]
        self.assertIn("Q: q4", prompt)
        self.assertIn("Q: q5", prompt)
        self.assertNotIn("Q: q3", prompt)  # older turns dropped

    def test_long_answers_are_truncated_in_history(self):
        llm = RecordingLLM()
        uc = AnswerQuestionUseCase(
            llm_client=llm, vector_store=OneHitVectors(), code_parser=NoTraceParser(),
            cache=StaticCache(), max_history_answer_chars=20,
        )
        history = [ConversationTurn(question="q", answer="y" * 500)]
        asyncio.run(uc.execute(question=Question(text="follow up", history=history), repo_full_name=REPO, commit_sha=SHA))
        _, prompt = llm.prompts[0]
        self.assertIn("A: " + "y" * 20 + "\n", prompt)
        self.assertNotIn("y" * 21, prompt)

    def test_citations_come_from_this_search_not_from_history(self):
        history = [ConversationTurn(question="earlier", answer="see app/ghost.py", cited_files=["app/ghost.py"])]
        answer, _ = ask(Question(text="and that?", history=history))
        self.assertEqual([str(c) for c in answer.citations], ["app/auth.py:1-1"])


if __name__ == "__main__":
    unittest.main()
