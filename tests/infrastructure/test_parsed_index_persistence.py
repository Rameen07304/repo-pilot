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
        self.cache = SqliteRepoCache(db_path=str(Path(self._dir.name) / "t.db"))

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


if __name__ == "__main__":
    unittest.main()
