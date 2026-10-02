"""
The MCP adapter: argument resolution and how results read to an agent.

The HTTP layer has its own tests, so these stub _post and check the two
things that are this file's own work: that a tool called before any
ingest says what to do about it, and that the evidence tool keeps the
line numbers attached to the right edges in both directions.

An agent cannot see a caveat it was not told, so "no edges recorded"
must not look like "no dependencies".
"""
import unittest
from unittest.mock import patch

# The MCP SDK is an optional extra (requirements-mcp.txt): the deployed
# web image has no use for it, so the suite must stay green without it
# rather than make every install carry a dependency one entry point
# needs.
try:
    from mcp.server.mcpserver.exceptions import ToolError

    import mcp_server
except ImportError:  # pragma: no cover - exercised by the install without the extra
    mcp_server = None
    ToolError = None

REPO = "acme/widgets"
SHA = "abc123def4567890"

EVIDENCE = {
    "repo_full_name": REPO,
    "commit_sha": SHA,
    "edges": [
        {"from_path": "app/main.py", "to_path": "app/db.py", "lines": [3, 7]},
        {"from_path": "app/api.py", "to_path": "app/main.py", "lines": [1]},
        {"from_path": "app/odd.py", "to_path": "app/db.py", "lines": []},
    ],
    "edges_total": 3,
    "edges_evidenced": 2,
    "files_in_repo": 5,
    "files_skipped": 1,
    "unparsed_extensions": {".rb": 2},
    "degraded_languages": ["dart"],
    "truncated": False,
}


def ingested():
    """Put the module in the state a successful ingest leaves it in."""
    mcp_server._current.clear()
    mcp_server._current.update({"repo_full_name": REPO, "commit_sha": SHA})


needs_mcp = unittest.skipIf(mcp_server is None, "MCP SDK not installed (see requirements-mcp.txt)")


@needs_mcp
class TargetResolutionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mcp_server._current.clear()

    async def test_a_tool_before_any_ingest_says_to_ingest(self):
        # Only ToolError's message reaches the model; anything else
        # arrives as a bare "Error executing tool", which would leave
        # the agent with no idea what to do next.
        with self.assertRaises(ToolError) as caught:
            mcp_server._target(None)
        self.assertIn("ingest_repo", str(caught.exception))

    async def test_asking_for_a_repo_that_is_not_the_current_one_is_refused(self):
        ingested()
        with self.assertRaises(ToolError) as caught:
            mcp_server._target("someone/else")
        self.assertIn("someone/else", str(caught.exception))
        self.assertIn("ingest_repo", str(caught.exception))

    async def test_naming_the_current_repo_explicitly_is_fine(self):
        ingested()
        self.assertEqual(mcp_server._target(REPO), (REPO, SHA))

    async def test_omitting_the_repo_uses_the_last_ingest(self):
        ingested()
        self.assertEqual(mcp_server._target(None), (REPO, SHA))


@needs_mcp
class EvidenceToolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ingested()

    async def call(self, **kwargs):
        with patch.object(mcp_server, "_post", return_value=EVIDENCE):
            return await mcp_server.architecture_evidence(**kwargs)

    async def test_one_file_reports_both_directions_with_their_lines(self):
        out = await self.call(file_path="app/main.py")

        self.assertIn("## Imports (1)", out)
        self.assertIn("app/db.py", out)
        self.assertIn("line 3, line 7", out)
        self.assertIn("## Imported by (1)", out)
        self.assertIn("app/api.py", out)
        self.assertIn("line 1", out)

    async def test_an_edge_with_no_line_says_so_rather_than_looking_cited(self):
        out = await self.call(file_path="app/odd.py")
        self.assertIn("no single line accounts for this", out)

    async def test_a_file_with_no_edges_does_not_read_as_no_dependencies(self):
        out = await self.call(file_path="app/untouched.py")
        # It must point at the blind spots, since an unparsed language
        # looks exactly like a file with no imports.
        self.assertIn("No edges recorded", out)
        self.assertIn("blind spots", out)

    async def test_the_whole_graph_leads_with_what_was_not_seen(self):
        out = await self.call()

        self.assertIn("2 of 3 edges", out)
        self.assertIn("1 file(s) unreadable", out)
        self.assertIn(".rb (2)", out)
        self.assertIn("dart", out)

    async def test_a_truncated_graph_still_states_the_real_total(self):
        cut = dict(EVIDENCE, truncated=True, edges_total=900)
        with patch.object(mcp_server, "_post", return_value=cut):
            out = await mcp_server.architecture_evidence()
        self.assertIn("2 of 900 edges", out)
        self.assertIn("real totals", out)


@needs_mcp
class CallSequenceToolTest(unittest.IsolatedAsyncioTestCase):
    """
    This tool used to hand the agent a whole HTML document. What matters
    now is that the compact form keeps the two things the page carried:
    the call-site line for every step, and the reasons a call is absent.
    """

    TRACE = {
        "repo_full_name": REPO,
        "commit_sha": SHA,
        "entry_file": "app/main.py",
        "entry_symbol": "run",
        "steps": [
            {"depth": 0, "from_file": "app/main.py", "from_symbol": "run",
             "to_file": "app/db.py", "to_symbol": "save", "line": 12, "is_recursive": False},
            {"depth": 1, "from_file": "app/db.py", "from_symbol": "save",
             "to_file": "app/db.py", "to_symbol": "save", "line": 30, "is_recursive": True},
        ],
        "unresolved_calls": {"json.dumps": 3},
        "ambiguous_calls": {"save": 2},
        "truncated": False,
        "max_depth": 2,
    }

    def setUp(self):
        ingested()

    async def call(self, trace):
        with patch.object(mcp_server, "_post", return_value=trace):
            return await mcp_server.call_sequence("app/main.py", "run")

    async def test_every_step_names_the_file_and_line_it_was_called_from(self):
        out = await self.call(self.TRACE)

        self.assertIn("run -> save()", out)
        self.assertIn("app/main.py:12", out)
        self.assertIn("app/db.py:30", out)
        self.assertNotIn("<html", out.lower())
        self.assertNotIn("<div", out.lower())

    async def test_recursion_is_marked_rather_than_expanded(self):
        self.assertIn("[recurses]", await self.call(self.TRACE))

    async def test_depth_survives_being_flattened_into_text(self):
        out = await self.call(self.TRACE)
        lines = [ln for ln in out.splitlines() if "->" in ln]
        self.assertTrue(lines[0].startswith("- "), lines[0])
        self.assertTrue(lines[1].startswith("  - "), lines[1])

    async def test_the_reasons_a_call_is_missing_are_stated(self):
        out = await self.call(self.TRACE)

        self.assertIn("Not shown", out)
        self.assertIn("json.dumps (3)", out)
        self.assertIn("save (2)", out)

    async def test_a_cut_trace_says_the_tail_is_missing(self):
        out = await self.call(dict(self.TRACE, truncated=True))
        self.assertIn("hit its cap", out)

    async def test_no_steps_does_not_read_as_no_behaviour(self):
        # A function that only calls libraries traces to nothing. Saying
        # just that would imply it does nothing at all.
        out = await self.call(dict(self.TRACE, steps=[]))
        self.assertIn("No calls into this repository", out)
        self.assertIn("json.dumps", out)


@needs_mcp
class PullRequestToolTest(unittest.IsolatedAsyncioTestCase):
    PR = {
        "repo_full_name": REPO,
        "number": 42,
        "title": "Split the parser",
        "state": "open",
        "author": "rameen",
        "base_sha": "base111aaa",
        "head_sha": "head222bbb",
        "added_nodes": ["app/new.py"],
        "removed_nodes": [],
        "moved_nodes": [{"from_path": "app/a.py", "to_path": "core/a.py"}],
        "added_edges": [{"from_path": "app/main.py", "to_path": "app/new.py", "lines": [4]}],
        "removed_edges": [],
        "rerouted_nodes": ["app/main.py"],
        "unchanged_edge_count": 7,
    }

    async def call(self, pr):
        with patch.object(mcp_server, "_post", return_value=pr):
            return await mcp_server.explain_pull_request(
                "https://github.com/acme/widgets/pull/42"
            )

    async def test_it_leads_with_the_pull_request_itself(self):
        out = await self.call(self.PR)

        self.assertIn("#42", out)
        self.assertIn("Split the parser", out)
        self.assertIn("rameen", out)
        self.assertNotIn("<html", out.lower())

    async def test_a_new_dependency_carries_the_line_it_appears_on(self):
        out = await self.call(self.PR)
        self.assertIn("app/main.py -> app/new.py", out)
        self.assertIn("line 4", out)

    async def test_a_move_is_one_fact_not_an_add_and_a_delete(self):
        self.assertIn("app/a.py -> core/a.py", await self.call(self.PR))

    async def test_a_pull_request_that_changes_no_structure_says_so(self):
        # Otherwise it renders as a heading and some zeroes, which reads
        # like the tool failed rather than like a real answer.
        quiet = dict(
            self.PR, added_nodes=[], removed_nodes=[], moved_nodes=[],
            added_edges=[], removed_edges=[], rerouted_nodes=[],
        )
        out = await self.call(dict(quiet, unchanged_edge_count=0))
        self.assertIn("Nothing structural changed", out)
        # "0 dependencies were untouched" reads like a finding.
        self.assertNotIn("0 dependencies were untouched", out)

    async def test_untouched_dependencies_are_counted_when_there_are_any(self):
        self.assertIn("7 dependencies were untouched", await self.call(self.PR))


@needs_mcp
class IngestToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_records_the_repo_for_later_tools(self):
        mcp_server._current.clear()
        payload = {
            "repo_full_name": REPO,
            "commit_sha": SHA,
            "file_count": 5,
            "detected_stack": ["python"],
            "summary": "A test project.",
            "was_cached": False,
            "skipped_file_count": 0,
            "degraded_languages": [],
        }
        with patch.object(mcp_server, "_post", return_value=payload):
            out = await mcp_server.ingest_repo("https://github.com/acme/widgets")

        self.assertEqual(mcp_server._target(None), (REPO, SHA))
        self.assertIn(SHA[:7], out)
        self.assertIn("A test project.", out)

    async def test_skipped_files_are_reported_on_the_way_in(self):
        # The agent is about to ask questions whose answers depend on
        # files that are not there. It should hear that first.
        mcp_server._current.clear()
        payload = {
            "repo_full_name": REPO,
            "commit_sha": SHA,
            "file_count": 5,
            "detected_stack": [],
            "summary": "",
            "was_cached": True,
            "skipped_file_count": 4,
            "degraded_languages": ["dart"],
        }
        with patch.object(mcp_server, "_post", return_value=payload):
            out = await mcp_server.ingest_repo("https://github.com/acme/widgets")

        self.assertIn("4 file(s) could not be read", out)
        self.assertIn("dart", out)
        self.assertIn("from cache", out)


if __name__ == "__main__":
    unittest.main()
