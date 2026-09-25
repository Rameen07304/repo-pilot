"""
RepoUrl is the SSRF boundary: it is the only thing standing between a
user-supplied string and the server making an outbound request with its
own GitHub token attached. Everything downstream trusts it because it
validates at construction.

That makes the regex worth testing directly rather than only through
use cases with fakes. The failures that matter here are the ones that
look like github.com and are not.
"""
import unittest
from dataclasses import FrozenInstanceError

from app.domain.value_objects import RepoUrl


class RepoUrlAcceptsTest(unittest.TestCase):
    def test_plain_repo_url(self):
        url = RepoUrl("https://github.com/acme/widgets")
        self.assertEqual(url.owner, "acme")
        self.assertEqual(url.repo, "widgets")
        self.assertEqual(url.full_name, "acme/widgets")

    def test_trailing_slash(self):
        self.assertEqual(RepoUrl("https://github.com/acme/widgets/").full_name, "acme/widgets")

    def test_dot_git_suffix_is_stripped(self):
        self.assertEqual(RepoUrl("https://github.com/acme/widgets.git").full_name, "acme/widgets")

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(RepoUrl("  https://github.com/acme/widgets  ").full_name, "acme/widgets")

    def test_names_with_dots_dashes_and_underscores(self):
        url = RepoUrl("https://github.com/my-org.io/some_repo.v2")
        self.assertEqual(url.owner, "my-org.io")
        self.assertEqual(url.repo, "some_repo.v2")


class RepoUrlRejectsTest(unittest.TestCase):
    """
    Each of these is a way of pointing somewhere that is not GitHub while
    looking like it is. A regex that accepts any of them is an SSRF.
    """

    REJECTED = [
        # Host confusion: the real host is not github.com.
        ("subdomain lookalike", "https://github.com.evil.com/acme/widgets"),
        ("prefix lookalike", "https://notgithub.com/acme/widgets"),
        ("suffix lookalike", "https://github.company.com/acme/widgets"),
        ("userinfo trick", "https://github.com@evil.com/acme/widgets"),
        ("userinfo with password", "https://github.com:x@evil.com/acme/widgets"),
        ("port then host", "https://evil.com:443/github.com/acme/widgets"),

        # Scheme downgrades and non-HTTP schemes.
        ("http not https", "http://github.com/acme/widgets"),
        ("scheme relative", "//github.com/acme/widgets"),
        ("no scheme", "github.com/acme/widgets"),
        ("git protocol", "git://github.com/acme/widgets"),
        ("ssh form", "git@github.com:acme/widgets.git"),
        ("file scheme", "file:///etc/passwd"),
        ("javascript scheme", "javascript:alert(1)"),

        # Internal targets, the actual point of an SSRF.
        ("localhost", "https://localhost/acme/widgets"),
        ("loopback ip", "https://127.0.0.1/acme/widgets"),
        ("link local metadata", "https://169.254.169.254/latest/meta-data"),
        ("private range", "https://192.168.1.1/acme/widgets"),

        # Path shapes that are not a repository.
        ("owner only", "https://github.com/acme"),
        ("too deep", "https://github.com/acme/widgets/tree/main"),
        ("empty owner", "https://github.com//widgets"),
        ("empty repo", "https://github.com/acme/"),
        ("path traversal", "https://github.com/acme/../../etc/passwd"),
        ("query string", "https://github.com/acme/widgets?x=1"),
        ("fragment", "https://github.com/acme/widgets#frag"),

        # Injected characters.
        ("newline", "https://github.com/acme/widgets\nHost: evil.com"),
        ("tab", "https://github.com/acme\t/widgets"),
        ("space inside", "https://github.com/acme/wid gets"),
        ("null byte", "https://github.com/acme/widgets\x00"),

        # Nothing.
        ("empty", ""),
        ("whitespace only", "   "),
    ]

    def test_every_lookalike_is_rejected(self):
        for label, candidate in self.REJECTED:
            with self.subTest(label=label, url=candidate):
                with self.assertRaises(ValueError):
                    RepoUrl(candidate)

    def test_uppercase_host_is_rejected_rather_than_silently_accepted(self):
        # The pattern is case sensitive on the host. Documenting the
        # behaviour either way: if this ever starts passing, the parsed
        # owner/repo must still be correct.
        with self.assertRaises(ValueError):
            RepoUrl("https://GitHub.com/acme/widgets")


class RepoUrlIsImmutableTest(unittest.TestCase):
    def test_raw_cannot_be_reassigned_after_validation(self):
        url = RepoUrl("https://github.com/acme/widgets")
        with self.assertRaises(FrozenInstanceError):
            url.raw = "https://evil.com/x/y"  # type: ignore[misc]

    def test_normalized_url_is_rebuilt_from_parsed_parts(self):
        # Not echoed back from the input, so a rejected-but-somehow-present
        # oddity in the raw string cannot survive into an outbound request.
        self.assertEqual(
            RepoUrl("https://github.com/acme/widgets.git/").normalized,
            "https://github.com/acme/widgets",
        )
