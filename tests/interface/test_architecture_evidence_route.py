"""
POST /api/architecture/evidence — the graph as data.

The HTML export already renders the line numbers, so what matters here
is that the JSON reports the SAME edges and the SAME blind spots, and
that it never implies completeness it has not got. An agent cannot see
the caveat paragraph a human reads at the top of the document, so the
caveats have to be fields.
"""
import unittest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.use_cases.export_architecture import RepoNotExportableError
from app.domain.entities import ArchitectureCoverage, ArchitectureExport, ExportedEdge
from app.infrastructure.security.rate_limiter import FixedWindowRateLimiter
from app.interface.routes import architecture_evidence

REPO = "acme/widgets"
SHA = "abc123def4567890"


def export(edges=None, coverage=None):
    return ArchitectureExport(
        repo_full_name=REPO,
        commit_sha=SHA,
        repo_url=f"https://github.com/{REPO}",
        detected_stack=["Python"],
        edges=edges
        if edges is not None
        else [
            ExportedEdge("app/main.py", "app/routes.py", [3]),
            ExportedEdge("app/routes.py", "app/db.py", [1, 9]),
        ],
        coverage=coverage or ArchitectureCoverage(files_in_repo=3, edges_total=2),
    )


class Container:
    def __init__(self, result, *, limit=100):
        self.trust_proxy_headers = False
        self.document_limiter = FixedWindowRateLimiter(max_events=limit, window_seconds=3600)
        self.architecture_export_use_case = AsyncMock()
        if isinstance(result, Exception):
            self.architecture_export_use_case.execute.side_effect = result
        else:
            self.architecture_export_use_case.execute.return_value = result


def client(result, *, limit=100):
    app = FastAPI()
    app.include_router(architecture_evidence.router)
    app.state.container = Container(result, limit=limit)
    return TestClient(app)


def post(result, **kwargs):
    return client(result, **kwargs).post(
        "/api/architecture/evidence",
        json={"repo_full_name": REPO, "commit_sha": SHA},
    )


class ArchitectureEvidenceRouteTest(unittest.TestCase):
    def test_every_edge_carries_the_lines_it_was_resolved_from(self):
        body = post(export()).json()

        self.assertEqual(body["edges_total"], 2)
        self.assertEqual(body["edges_evidenced"], 2)
        by_pair = {(e["from_path"], e["to_path"]): e["lines"] for e in body["edges"]}
        self.assertEqual(by_pair[("app/main.py", "app/routes.py")], [3])
        self.assertEqual(by_pair[("app/routes.py", "app/db.py")], [1, 9])

    def test_an_edge_no_line_accounts_for_is_reported_not_dropped(self):
        # Silently omitting it would make the graph look fully evidenced,
        # which is the one thing this endpoint exists to not do.
        body = post(
            export(edges=[ExportedEdge("a.py", "b.py", []), ExportedEdge("b.py", "c.py", [2])])
        ).json()

        self.assertEqual(body["edges_total"], 2)
        self.assertEqual(body["edges_evidenced"], 1)
        self.assertEqual(len(body["edges"]), 2)
        self.assertEqual([e for e in body["edges"] if e["from_path"] == "a.py"][0]["lines"], [])

    def test_the_blind_spots_are_fields_not_prose(self):
        body = post(
            export(
                coverage=ArchitectureCoverage(
                    files_in_repo=10,
                    files_skipped=2,
                    unparsed_extensions={".rb": 3},
                    degraded_languages=["dart"],
                )
            )
        ).json()

        self.assertEqual(body["files_in_repo"], 10)
        self.assertEqual(body["files_skipped"], 2)
        self.assertEqual(body["unparsed_extensions"], {".rb": 3})
        self.assertEqual(body["degraded_languages"], ["dart"])

    def test_a_huge_graph_is_cut_but_still_reports_the_true_total(self):
        many = [ExportedEdge(f"f{i}.py", "core.py", [i + 1]) for i in range(450)]
        body = post(export(edges=many)).json()

        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["edges"]), architecture_evidence.MAX_EDGES)
        self.assertEqual(body["edges_total"], 450, "the count must be the real one, not the cut one")

    def test_a_graph_that_fits_is_not_marked_truncated(self):
        self.assertFalse(post(export()).json()["truncated"])

    def test_uningested_repo_is_a_404(self):
        res = post(RepoNotExportableError("not ingested"))
        self.assertEqual(res.status_code, 404)

    def test_the_document_budget_applies(self):
        # Parse-only, so it must not spend the question allowance, but it
        # is not free either.
        c = client(export(), limit=1)
        body = {"repo_full_name": REPO, "commit_sha": SHA}
        self.assertEqual(c.post("/api/architecture/evidence", json=body).status_code, 200)
        self.assertEqual(c.post("/api/architecture/evidence", json=body).status_code, 429)


if __name__ == "__main__":
    unittest.main()
