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
