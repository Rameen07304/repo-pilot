"""
DeepInvestigationUseCase — the bounded search loop. The LLM is scripted:
its planner replies drive the loop, its synthesis reply is canned. The
vector store returns a different result set per search phrase so the
tests can see which rounds contributed evidence.
"""
import asyncio
import unittest

from app.application.interfaces.vector_store import VectorQueryResult
from app.application.use_cases.investigate_question import DeepInvestigationUseCase
from app.domain.entities import CodeChunk, FileNode, ParsedRepoIndex, Question, RepoManifest
from app.domain.value_objects import QuestionMode, RepoUrl

REPO = "acme/widgets"
SHA = "abc123"


def chunk(path: str, start: int = 1, end: int = 5) -> CodeChunk:
    return CodeChunk(id=f"{path}:{start}", file_path=path, content=f"# code in {path}", start_line=start, end_line=end)


class ScriptedLLM:
    """embed() records the phrase; generate() pops the next scripted reply."""

    def __init__(self, planner_replies: list[str]):
        self.embedded: list[str] = []
        self.replies = list(planner_replies)
        self.generate_calls = 0

    async def embed(self, texts):
        self.embedded.extend(texts)
        return [[float(len(self.embedded) - 1)] for _ in texts]  # index of this phrase

    async def generate(self, prompt, **kwargs):
        self.generate_calls += 1
        if self.replies:
            return self.replies.pop(0)
        return "SYNTHESIZED ANSWER"


class ScriptedVectors:
    """Result set N is returned for the N-th phrase embedded."""

    def __init__(self, result_sets: list[list[VectorQueryResult]]):
        self.result_sets = result_sets

    async def query(self, *, repo_full_name, commit_sha, query_embedding, top_k):
        index = int(query_embedding[0])
        return self.result_sets[index] if index < len(self.result_sets) else []


class NoTraceParser:
    def trace_calls(self, **kwargs):
        return []


class StaticCache:
    def __init__(self, paths):
        self.manifest = RepoManifest(
            repo_url=RepoUrl(f"https://github.com/{REPO}"), commit_sha=SHA,
            files=[FileNode(path=p, size_bytes=10, content="x") for p in paths],
        )
        self.index = ParsedRepoIndex(repo_full_name=REPO, commit_sha=SHA, symbols=[], module_edges=[])

    async def get(self, repo_full_name, commit_sha):
        return self.manifest

    async def get_parsed_index(self, repo_full_name, commit_sha):
        return self.index


def run(planner_replies, result_sets, max_steps=5):
    llm = ScriptedLLM(planner_replies)
    paths = sorted({r.chunk.file_path for rs in result_sets for r in rs})
    uc = DeepInvestigationUseCase(
        llm_client=llm, vector_store=ScriptedVectors(result_sets), code_parser=NoTraceParser(),
        cache=StaticCache(paths), max_steps=max_steps,
    )
    answer = asyncio.run(uc.execute(question=Question(text="how does auth work?", mode=QuestionMode.DEEP), repo_full_name=REPO, commit_sha=SHA))
    return answer, llm


def hit(path, sim):
    return VectorQueryResult(chunk=chunk(path), similarity=sim)


class DeepInvestigationTest(unittest.TestCase):
    def test_follows_planner_until_sufficient_and_merges_evidence(self):
        answer, llm = run(
            planner_replies=[
                '{"sufficient": false, "missing": "where the token is verified", "next_search": "verify token"}',
                '{"sufficient": true, "missing": "", "next_search": ""}',
            ],
            result_sets=[[hit("app/routes.py", 0.8)], [hit("app/security/jwt.py", 0.7)]],
        )
        self.assertEqual(answer.mode_used, QuestionMode.DEEP)
        self.assertEqual(answer.steps_taken, 2)
        self.assertEqual(llm.embedded, ["how does auth work?", "verify token"])
        self.assertEqual({c.file_path for c in answer.citations}, {"app/routes.py", "app/security/jwt.py"})
        self.assertEqual(answer.text, "SYNTHESIZED ANSWER")
        self.assertEqual(answer.confidence.value, 0.8)  # best hit across all rounds
        self.assertIn("still unclear: where the token is verified", answer.investigation_log)
        self.assertIn("stopped: evidence judged sufficient", answer.investigation_log)
        self.assertEqual(llm.generate_calls, 3)  # 2 planner + 1 synthesis

    def test_stops_at_step_limit(self):
        never_enough = '{"sufficient": false, "missing": "more", "next_search": "phrase %d"}'
        answer, llm = run(
            planner_replies=[never_enough % i for i in range(10)],
            result_sets=[[hit(f"f{i}.py", 0.5)] for i in range(10)],
            max_steps=3,
        )
        self.assertEqual(answer.steps_taken, 3)
        self.assertEqual(len(llm.embedded), 3)
        self.assertIn("stopped: reached the 3-step limit", answer.investigation_log)
        self.assertEqual(llm.generate_calls, 2 + 1)  # planner only runs between rounds, never after the last

    def test_stops_when_search_adds_nothing_new(self):
        same = hit("app/routes.py", 0.8)
        answer, _ = run(
            planner_replies=['{"sufficient": false, "missing": "", "next_search": "routes again"}'],
            result_sets=[[same], [same]],
        )
        self.assertEqual(answer.steps_taken, 2)
        self.assertIn("stopped: the last search added nothing new", answer.investigation_log)
        self.assertEqual(len(answer.citations), 1)

    def test_stops_when_planner_repeats_a_search(self):
        answer, _ = run(
            planner_replies=['{"sufficient": false, "missing": "", "next_search": "How Does Auth Work?"}'],
            result_sets=[[hit("app/routes.py", 0.8)]],
        )
        self.assertEqual(answer.steps_taken, 1)
        self.assertIn("stopped: planner repeated an earlier search", answer.investigation_log)

    def test_garbage_planner_reply_stops_gracefully(self):
        answer, _ = run(
            planner_replies=["Sure! Let me think about that..."],
            result_sets=[[hit("app/routes.py", 0.8)]],
        )
        self.assertEqual(answer.steps_taken, 1)
        self.assertEqual(answer.text, "SYNTHESIZED ANSWER")
        self.assertIn("stopped: could not plan a further step", answer.investigation_log)

    def test_planner_cannot_inject_citations(self):
        # The planner names a path; citations must still come only from retrieval.
        answer, llm = run(
            planner_replies=['{"sufficient": false, "missing": "", "next_search": "../../etc/passwd"}',
                             '{"sufficient": true, "missing": "", "next_search": ""}'],
            result_sets=[[hit("app/routes.py", 0.8)], [hit("app/models.py", 0.6)]],
        )
        self.assertEqual(llm.embedded[1], "../../etc/passwd")  # it is only ever a search phrase
        self.assertEqual({c.file_path for c in answer.citations}, {"app/routes.py", "app/models.py"})

    def test_no_evidence_at_all(self):
        answer, llm = run(planner_replies=[], result_sets=[[]])
        self.assertEqual(answer.steps_taken, 1)
        self.assertEqual(answer.citations, [])
        self.assertEqual(answer.confidence.value, 0.0)
        self.assertEqual(llm.generate_calls, 0)  # no planner, no synthesis


if __name__ == "__main__":
    unittest.main()
