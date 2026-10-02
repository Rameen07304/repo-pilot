"""
The sequence trace and the pull request, as data.

These mirror endpoints that already return a finished HTML page. What
is worth testing is not that JSON comes out, but that the parts a
reader gets from the page's margin -- the calls that left the
repository, the names that were ambiguous, the trace that was cut --
survive the trip as fields. An agent that cannot see those reads a
partial trace as a complete one.
"""
import unittest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.interfaces.github_client import PullRequestRef
from app.application.use_cases.trace_call_sequence import SequenceNotTraceableError
from app.domain.entities import (
    ArchitectureCoverage,
    ArchitectureDelta,
    ArchitectureExport,
    CallSequence,
    CallStep,
    ChangedEdge,
    MovedNode,
)
from app.infrastructure.security.rate_limiter import FixedWindowRateLimiter
from app.interface.routes import agent_data

REPO = "acme/widgets"
SHA = "abc123def4567890"


def sequence(steps=None, **kwargs):
    defaults = dict(
        repo_full_name=REPO,
        commit_sha=SHA,
        repo_url=f"https://github.com/{REPO}",
        entry_file="app/main.py",
        entry_symbol="run",
        steps=steps
        if steps is not None
        else [
            CallStep(0, "app/main.py", "run", "app/db.py", "save", 12),
            CallStep(1, "app/db.py", "save", "app/db.py", "save", 30, is_recursive=True),
        ],
        max_depth=2,
    )
    defaults.update(kwargs)
    return CallSequence(**defaults)


def export(name=REPO, sha=SHA):
    return ArchitectureExport(
        repo_full_name=name,
        commit_sha=sha,
        repo_url=f"https://github.com/{name}",
        coverage=ArchitectureCoverage(),
    )


def delta(**kwargs):
    defaults = dict(
        repo_full_name=REPO,
        base=export(sha="base111"),
        head=export(sha="head222"),
        added_nodes=["app/new.py"],
        removed_nodes=["app/old.py"],
        moved_nodes=[MovedNode("app/a.py", "core/a.py")],
        added_edges=[ChangedEdge("app/main.py", "app/new.py", [4])],
        removed_edges=[ChangedEdge("app/main.py", "app/old.py", [5])],
        rerouted_nodes=["app/main.py"],
        unchanged_edge_count=7,
    )
    defaults.update(kwargs)
    return ArchitectureDelta(**defaults)


PULL = PullRequestRef(
    number=42, title="Split the parser", base_sha="base111", head_sha="head222",
    state="open", author="rameen",
)


class Container:
    def __init__(self, *, seq=None, pr=None, limit=100):
        self.trust_proxy_headers = False
        self.document_limiter = FixedWindowRateLimiter(max_events=limit, window_seconds=3600)
        self.question_limiter = FixedWindowRateLimiter(max_events=limit, window_seconds=3600)
        self.sequence_use_case = AsyncMock()
        self.pull_request_use_case = AsyncMock()
        if isinstance(seq, Exception):
            self.sequence_use_case.execute.side_effect = seq
        else:
            self.sequence_use_case.execute.return_value = seq
        self.pull_request_use_case.execute.return_value = pr


def client(**kwargs):
    app = FastAPI()
    app.include_router(agent_data.router)
    app.state.container = Container(**kwargs)
    return TestClient(app)


def trace(seq, **kwargs):
    return client(seq=seq, **kwargs).post(
        "/api/sequence/steps",
        json={
            "repo_full_name": REPO, "commit_sha": SHA,
            "entry_file": "app/main.py", "entry_symbol": "run",
        },
    )


class SequenceStepsRouteTest(unittest.TestCase):
    def test_each_step_carries_the_call_site_line(self):
        body = trace(sequence()).json()

        self.assertEqual(len(body["steps"]), 2)
        first = body["steps"][0]
        self.assertEqual(first["from_symbol"], "run")
        self.assertEqual(first["to_symbol"], "save")
        self.assertEqual(first["line"], 12)
        self.assertEqual(first["depth"], 0)
        self.assertTrue(body["steps"][1]["is_recursive"])

    def test_calls_that_left_the_repository_are_reported_not_dropped(self):
        body = trace(
            sequence(unresolved_calls={"json.dumps": 3}, ambiguous_calls={"save": 2})
        ).json()

        self.assertEqual(body["unresolved_calls"], {"json.dumps": 3})
        self.assertEqual(body["ambiguous_calls"], {"save": 2})

    def test_a_long_trace_is_cut_and_says_so(self):
        many = [CallStep(0, "a.py", "f", "b.py", "g", i + 1) for i in range(400)]
        body = trace(sequence(steps=many)).json()

        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["steps"]), agent_data.MAX_STEPS)

    def test_a_trace_the_use_case_already_cut_stays_marked(self):
        # Two different caps; losing either one would overstate the trace.
        body = trace(sequence(truncated=True)).json()
        self.assertTrue(body["truncated"])

    def test_an_untraceable_entry_is_a_404(self):
        self.assertEqual(trace(SequenceNotTraceableError("no such symbol")).status_code, 404)

    def test_the_document_budget_applies(self):
        c = client(seq=sequence(), limit=1)
        payload = {
            "repo_full_name": REPO, "commit_sha": SHA,
            "entry_file": "app/main.py", "entry_symbol": "run",
        }
        self.assertEqual(c.post("/api/sequence/steps", json=payload).status_code, 200)
        self.assertEqual(c.post("/api/sequence/steps", json=payload).status_code, 429)


class PullRequestDataRouteTest(unittest.TestCase):
    def post(self, **kwargs):
        return client(pr=(delta(), PULL), **kwargs).post(
            "/api/architecture/pull/data",
            json={"pull_request_url": "https://github.com/acme/widgets/pull/42"},
        )

    def test_it_reports_the_pull_request_and_both_ends(self):
        body = self.post().json()

        self.assertEqual(body["number"], 42)
        self.assertEqual(body["title"], "Split the parser")
        self.assertEqual(body["author"], "rameen")
        self.assertEqual(body["base_sha"], "base111")
        self.assertEqual(body["head_sha"], "head222")

    def test_changed_edges_keep_the_lines_they_came_from(self):
        body = self.post().json()

        self.assertEqual(body["added_edges"][0]["lines"], [4])
        self.assertEqual(body["removed_edges"][0]["lines"], [5])

    def test_rerouted_files_survive_as_their_own_field(self):
        # The single most useful column in a review: files that kept
        # their path but changed what they depend on.
        self.assertEqual(self.post().json()["rerouted_nodes"], ["app/main.py"])

    def test_moves_are_reported_as_moves_not_as_an_add_and_a_delete(self):
        moved = self.post().json()["moved_nodes"]
        self.assertEqual(moved, [{"from_path": "app/a.py", "to_path": "core/a.py"}])

    def test_the_question_budget_applies(self):
        c = client(pr=(delta(), PULL), limit=1)
        payload = {"pull_request_url": "https://github.com/acme/widgets/pull/42"}
        self.assertEqual(c.post("/api/architecture/pull/data", json=payload).status_code, 200)
        self.assertEqual(c.post("/api/architecture/pull/data", json=payload).status_code, 429)


if __name__ == "__main__":
    unittest.main()
