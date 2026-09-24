"""
The store needs a lid. Anyone can make a public deployment ingest an
arbitrary public repository, so without eviction the disk fills and
every repo becomes unusable at once.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from app.domain.entities import ParsedRepoIndex, RepoManifest
from app.domain.value_objects import RepoUrl
from app.infrastructure.cache.sqlite_cache import SqliteRepoCache


def manifest(name, sha="sha1"):
    return RepoManifest(
        repo_url=RepoUrl(f"https://github.com/{name}"),
        commit_sha=sha,
        files=[],
        detected_stack=["Python"],
    )


class CacheEvictionTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db = str(Path(self._dir.name) / "t.db")

    def tearDown(self):
        self._dir.cleanup()

    def test_uncapped_cache_keeps_everything(self):
        cache = SqliteRepoCache(db_path=self.db, max_repos=0)
        for i in range(6):
            asyncio.run(cache.set(manifest(f"acme/r{i}")))
        kept = [i for i in range(6) if asyncio.run(cache.get(f"acme/r{i}", "sha1"))]
        self.assertEqual(len(kept), 6)

    def test_capped_cache_evicts_the_oldest(self):
        cache = SqliteRepoCache(db_path=self.db, max_repos=3)
        for i in range(5):
            asyncio.run(cache.set(manifest(f"acme/r{i}")))
        kept = [i for i in range(5) if asyncio.run(cache.get(f"acme/r{i}", "sha1"))]
        self.assertEqual(len(kept), 3, f"expected 3 kept, got {kept}")
        # The most recent survive.
        self.assertIn(4, kept)

    def test_eviction_takes_the_parsed_index_with_it(self):
        # Leaving a parsed index behind for an evicted manifest would grow
        # the half of the store that is actually large.
        cache = SqliteRepoCache(db_path=self.db, max_repos=2)
        for i in range(4):
            asyncio.run(cache.set(manifest(f"acme/r{i}")))
            asyncio.run(cache.set_parsed_index(
                ParsedRepoIndex(repo_full_name=f"acme/r{i}", commit_sha="sha1")
            ))
        orphans = [
            i for i in range(4)
            if asyncio.run(cache.get(f"acme/r{i}", "sha1")) is None
            and asyncio.run(cache.get_parsed_index(f"acme/r{i}", "sha1")) is not None
        ]
        self.assertEqual(orphans, [], "parsed indexes outlived their manifests")


class LimiterMemoryTest(unittest.TestCase):
    """
    The window dict holds one entry per key seen. Unpruned that is an
    unbounded leak on a public deployment, and a cheap way to push a
    small container over its memory limit.
    """

    def test_expired_windows_are_dropped(self):
        from app.infrastructure.security.rate_limiter import (
            _PRUNE_EVERY,
            FixedWindowRateLimiter,
        )

        limiter = FixedWindowRateLimiter(max_events=100, window_seconds=1)
        for i in range(_PRUNE_EVERY):
            limiter.check_and_increment(f"ip-{i}")
        self.assertEqual(len(limiter._windows), _PRUNE_EVERY)

        # Age every window past the window, then trip the sweep.
        for window in limiter._windows.values():
            window.window_start -= 10

        for i in range(_PRUNE_EVERY):
            limiter.check_and_increment(f"later-{i}")

        self.assertLessEqual(
            len(limiter._windows), _PRUNE_EVERY + 1,
            "expired windows were never released",
        )

    def test_active_windows_survive_a_sweep(self):
        from app.infrastructure.security.rate_limiter import (
            _PRUNE_EVERY,
            FixedWindowRateLimiter,
        )

        limiter = FixedWindowRateLimiter(max_events=100, window_seconds=3600)
        for i in range(_PRUNE_EVERY + 5):
            limiter.check_and_increment(f"ip-{i}")
        # Nothing has expired, so nothing may be dropped: losing a live
        # window would reset somebody's count and let them past the limit.
        self.assertEqual(len(limiter._windows), _PRUNE_EVERY + 5)
