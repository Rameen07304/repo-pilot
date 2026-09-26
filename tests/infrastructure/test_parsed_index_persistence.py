"""
What a cached parse must remember about itself.

A parsed index outlives the code that built it, so two fields have to
survive the round trip or the cache quietly misbehaves: the parser
version it was built with, and the line each edge came from.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from app.domain.entities import ParsedRepoIndex
from app.infrastructure.cache.sqlite_cache import SqliteRepoCache

REPO = "acme/widgets"
SHA = "sha1"


def index(**kwargs):
    defaults = dict(
        repo_full_name=REPO,
        commit_sha=SHA,
        module_edges=[("app/main.py", "app/db.py")],
        edge_provenance=[("app/main.py", "app/db.py", 4)],
        parser_version="7",
    )
    defaults.update(kwargs)
    return ParsedRepoIndex(**defaults)


class ParsedIndexPersistenceTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._dir.name) / "t.db")
        self.cache = SqliteRepoCache(db_path=self.db_path)

    def tearDown(self):
        self._dir.cleanup()

    def round_trip(self, value):
        asyncio.run(self.cache.set_parsed_index(value))
        return asyncio.run(self.cache.get_parsed_index(REPO, SHA))

    def test_the_parser_version_survives_the_cache(self):
        # It was read back on load but never written, so every cached
        # index deserialised as version "" — which never matches the
        # current parser, so the staleness check discarded all of them and
        # nothing was ever reused. The symptom is a slow cache hit, not an
        # error, which is exactly why it needs a test.
        self.assertEqual(self.round_trip(index()).parser_version, "7")

    def test_edge_lines_survive_the_cache(self):
        self.assertEqual(
            self.round_trip(index()).edge_provenance, [("app/main.py", "app/db.py", 4)]
        )

    def test_provenance_written_before_it_existed_loads_as_absent(self):
        # Rows predating this column must still load; they come back with
        # no lines rather than failing, and the version check re-parses
        # them on next use.
        restored = self.round_trip(index(edge_provenance=[], parser_version="2"))
        self.assertEqual(restored.edge_provenance, [])
        self.assertEqual(restored.module_edges, [("app/main.py", "app/db.py")])

    def test_provenance_lookup_finds_every_line_for_one_edge(self):
        restored = self.round_trip(
            index(
                edge_provenance=[
                    ("app/main.py", "app/db.py", 9),
                    ("app/main.py", "app/db.py", 2),
                    ("app/other.py", "app/db.py", 5),
                ]
            )
        )
        self.assertEqual(restored.provenance_for("app/main.py", "app/db.py"), [2, 9])
        self.assertEqual(restored.provenance_for("app/main.py", "nope.py"), [])


class ListCommitsTest(unittest.TestCase):
    """
    Which commits can be compared. A delta needs both sides parsed, so a
    manifest whose parse is missing must not be offered: the picker would
    be showing a choice that can only fail.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._dir.name) / "t.db")
        self.cache = SqliteRepoCache(db_path=self.db_path)

    def tearDown(self):
        self._dir.cleanup()

    def manifest(self, sha):
        from app.domain.entities import RepoManifest
        from app.domain.value_objects import RepoUrl

        return RepoManifest(
            repo_url=RepoUrl(f"https://github.com/{REPO}"), commit_sha=sha, files=[]
        )

    def test_a_repo_nobody_ingested_lists_nothing(self):
        self.assertEqual(asyncio.run(self.cache.list_commits("nobody/here")), [])

    def test_only_commits_with_a_parse_are_offered(self):
        asyncio.run(self.cache.set(self.manifest("parsed")))
        asyncio.run(self.cache.set(self.manifest("unparsed")))
        asyncio.run(self.cache.set_parsed_index(index(commit_sha="parsed")))

        shas = [c.commit_sha for c in asyncio.run(self.cache.list_commits(REPO))]
        self.assertEqual(shas, ["parsed"])

    def test_commits_come_back_newest_first(self):
        # The timestamps are forced apart afterwards: three rows written
        # in the same millisecond tie, and a tied list is already sorted,
        # so the assertion would hold even if the ORDER BY were wrong.
        import sqlite3

        for sha in ("oldest", "middle", "newest"):
            asyncio.run(self.cache.set(self.manifest(sha)))
            asyncio.run(self.cache.set_parsed_index(index(commit_sha=sha)))

        conn = sqlite3.connect(self.db_path)
        for sha, when in (
            ("oldest", "2026-01-01T00:00:00+00:00"),
            ("middle", "2026-06-01T00:00:00+00:00"),
            ("newest", "2026-09-01T00:00:00+00:00"),
        ):
            conn.execute(
                "UPDATE repo_manifests SET cached_at=? WHERE commit_sha=?", (when, sha)
            )
        conn.commit()
        conn.close()

        commits = asyncio.run(self.cache.list_commits(REPO))
        self.assertEqual([c.commit_sha for c in commits], ["newest", "middle", "oldest"])

    def test_a_row_with_an_unreadable_date_is_skipped_not_fatal(self):
        import sqlite3

        asyncio.run(self.cache.set(self.manifest("good")))
        asyncio.run(self.cache.set_parsed_index(index(commit_sha="good")))
        asyncio.run(self.cache.set(self.manifest("bad")))
        asyncio.run(self.cache.set_parsed_index(index(commit_sha="bad")))

        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE repo_manifests SET cached_at='not a date' WHERE commit_sha='bad'")
        conn.commit()
        conn.close()

        self.assertEqual(
            [c.commit_sha for c in asyncio.run(self.cache.list_commits(REPO))], ["good"]
        )


if __name__ == "__main__":
    unittest.main()
