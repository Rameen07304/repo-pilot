"""
HTTP contracts for the routes added for onboarding, source, impact and
streaming.

The app is built with a hand-made container rather than
build_container(), so these run with no API keys, no network, no
Chroma and no SQLite — they test the route layer's wiring, status
codes and payload shape, not the use cases (those have their own
tests).
"""
import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.use_cases.analyze_impact import AnalyzeImpactUseCase
from app.application.use_cases.generate_onboarding import GenerateOnboardingUseCase
from app.application.use_cases.get_file_source import GetFileSourceUseCase
from app.domain.entities import FileNode, ParsedRepoIndex, ParsedSymbol, RepoManifest
from app.domain.value_objects import RepoUrl
from app.infrastructure.security.rate_limiter import FixedWindowRateLimiter
from app.interface.routes import impact, onboarding, source

REPO = "acme/app"
SHA = "sha1"


class StubCache:
    """Two files, one import edge, one symbol — enough to exercise every route."""

    def __init__(self, *, empty=False):
        if empty:
            self.manifest = None
            self.index = None
            return
        self.manifest = RepoManifest(
            repo_url=RepoUrl(f"https://github.com/{REPO}"),
            commit_sha=SHA,
            files=[
                FileNode(path="app/main.py", size_bytes=20, language="python",
                         content="import core\n\ndef run():\n    pass\n"),
                FileNode(path="core/db.py", size_bytes=10, language="python",
                         content="def save():\n    pass\n"),
            ],
            detected_stack=["python"],
            entry_points=["app/main.py"],
            summary="A test project.",
        )
        self.index = ParsedRepoIndex(
            repo_full_name=REPO,
            commit_sha=SHA,
            symbols=[
                ParsedSymbol(name="run", kind="function", file_path="app/main.py",
                             start_line=3, end_line=4, calls=["save"]),
                ParsedSymbol(name="save", kind="function", file_path="core/db.py",
                             start_line=1, end_line=2),
            ],
            module_edges=[("app/main.py", "core/db.py")],
        )

    async def get(self, repo_full_name, commit_sha):
        return self.manifest

    async def get_parsed_index(self, repo_full_name, commit_sha):
        return self.index


class Container:
    def __init__(self, cache, *, limit=100):
        self.trust_proxy_headers = False
        self.question_limiter = FixedWindowRateLimiter(max_events=limit, window_seconds=3600)
        self.deep_question_limiter = FixedWindowRateLimiter(max_events=limit, window_seconds=3600)
        self.daily_ip_limiter = FixedWindowRateLimiter(max_events=limit, window_seconds=86400)
        self.onboarding_use_case = GenerateOnboardingUseCase(
            cache=cache, llm_client=None, use_llm_narration=False
        )
        self.file_source_use_case = GetFileSourceUseCase(cache=cache)
        self.impact_use_case = AnalyzeImpactUseCase(
            cache=cache, llm_client=None, use_llm_summary=False
        )


def client(*, empty=False, limit=100):
    app = FastAPI()
    app.include_router(onboarding.router)
    app.include_router(source.router)
    app.include_router(impact.router)
    app.state.container = Container(StubCache(empty=empty), limit=limit)
    return TestClient(app)


def keys():
    return {"repo_full_name": REPO, "commit_sha": SHA}


class OnboardingRouteTest(unittest.TestCase):
    def test_returns_a_guide_for_an_ingested_repo(self):
        res = client().post("/api/onboarding", json=keys())
        self.assertEqual(res.status_code, 200)
        body = res.json()

        self.assertEqual(body["repo_full_name"], REPO)
        self.assertIn("app/main.py", body["entry_points"])
        self.assertTrue(body["reading_order"])
        self.assertTrue(body["suggested_questions"])
        self.assertTrue(body["graph_available"])

    def test_every_reading_step_is_fully_populated(self):
        body = client().post("/api/onboarding", json=keys()).json()
        for step in body["reading_order"]:
            self.assertTrue(step["file_path"])
            self.assertTrue(step["why_it_matters"])
            self.assertTrue(step["what_to_understand"])
            self.assertIsInstance(step["is_entry_point"], bool)

    def test_uningested_repo_is_a_404(self):
        res = client(empty=True).post("/api/onboarding", json=keys())
        self.assertEqual(res.status_code, 404)

    def test_rate_limit_is_enforced(self):
        c = client(limit=1)
        self.assertEqual(c.post("/api/onboarding", json=keys()).status_code, 200)
        self.assertEqual(c.post("/api/onboarding", json=keys()).status_code, 429)


class FileSourceRouteTest(unittest.TestCase):
    def test_returns_source_with_symbols_and_both_edge_directions(self):
        res = client().post("/api/file", json={**keys(), "file_path": "core/db.py"})
        self.assertEqual(res.status_code, 200)
        body = res.json()

        self.assertIn("def save()", body["content"])
        self.assertEqual(body["language"], "python")
        self.assertEqual([s["name"] for s in body["symbols"]], ["save"])
        self.assertEqual(body["imported_by"], ["app/main.py"])
        self.assertEqual(body["imports"], [])

    def test_unknown_file_is_a_404(self):
        res = client().post("/api/file", json={**keys(), "file_path": "nope.py"})
        self.assertEqual(res.status_code, 404)

    def test_traversal_path_is_a_404_not_a_read(self):
        res = client().post("/api/file", json={**keys(), "file_path": "../../etc/passwd"})
        self.assertEqual(res.status_code, 404)

    def test_missing_file_path_is_a_422(self):
        self.assertEqual(client().post("/api/file", json=keys()).status_code, 422)


class ImpactRouteTest(unittest.TestCase):
    def test_reports_the_direct_blast_radius(self):
        res = client().post("/api/impact", json={**keys(), "file_path": "core/db.py"})
        self.assertEqual(res.status_code, 200)
        body = res.json()

        self.assertEqual([n["path"] for n in body["direct"]], ["app/main.py"])
        self.assertTrue(body["direct"][0]["is_direct"])
        self.assertEqual(body["indirect"], [])
        self.assertEqual(body["affected_files"], ["app/main.py"])
        self.assertEqual(body["entry_points_reached"], ["app/main.py"])
        self.assertTrue(body["summary"])

    def test_symbol_scoped_analysis_reports_callers(self):
        res = client().post(
            "/api/impact", json={**keys(), "file_path": "core/db.py", "symbol": "save"}
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual([n["path"] for n in body["callers"]], ["app/main.py"])
        self.assertEqual(body["callers"][0]["symbols"], ["run"])

    def test_unknown_file_is_a_404(self):
        res = client().post("/api/impact", json={**keys(), "file_path": "ghost.py"})
        self.assertEqual(res.status_code, 404)

    def test_unknown_symbol_is_a_404(self):
        res = client().post(
            "/api/impact", json={**keys(), "file_path": "core/db.py", "symbol": "ghost"}
        )
        self.assertEqual(res.status_code, 404)

    def test_uningested_repo_is_a_404(self):
        res = client(empty=True).post("/api/impact", json={**keys(), "file_path": "a.py"})
        self.assertEqual(res.status_code, 404)


if __name__ == "__main__":
    unittest.main()


class StreamHistoryParsingTest(unittest.TestCase):
    """The history query parameter is attacker-editable, so bad input degrades to no history."""

    def setUp(self):
        from app.interface.routes.investigate_stream import _parse_history
        self.parse = _parse_history

    def test_empty_and_malformed_yield_no_history(self):
        for raw in ["", "   ", "not json", "{}", '"a string"', "[1, 2, 3]", '[{"no_question": 1}]']:
            self.assertEqual(self.parse(raw), [], f"for {raw!r}")

    def test_well_formed_history_is_parsed(self):
        turns = self.parse('[{"question": "q1", "answer": "a1", "cited_files": ["app/a.py"]}]')
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].question, "q1")
        self.assertEqual(turns[0].answer, "a1")
        self.assertEqual(turns[0].cited_files, ["app/a.py"])

    def test_fields_are_length_and_count_capped(self):
        raw = json.dumps([{"question": "q" * 900, "answer": "a" * 9000, "cited_files": ["f"] * 50}])
        turns = self.parse(raw)
        self.assertEqual(len(turns[0].question), 500)
        self.assertEqual(len(turns[0].answer), 4000)
        self.assertEqual(len(turns[0].cited_files), 20)

    def test_turn_count_is_capped_keeping_the_most_recent(self):
        raw = json.dumps([{"question": f"q{i}"} for i in range(30)])
        turns = self.parse(raw, max_turns=3)
        self.assertEqual([t.question for t in turns], ["q27", "q28", "q29"])

    def test_wrong_types_inside_a_turn_are_coerced_away(self):
        turns = self.parse('[{"question": "q", "answer": 42, "cited_files": "not-a-list"}]')
        self.assertEqual(turns[0].answer, "")
        self.assertEqual(turns[0].cited_files, [])
