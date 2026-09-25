"""
The redaction boundary. Everything that reaches an LLM prompt or the
vector store passes through scan_and_redact first, so a pattern that
misses means a credential leaves the machine, and a pattern that is too
greedy means answers are full of [REDACTED_SECRET] where ordinary code
used to be.

Tested directly, with a table of what must and must not match, because
going through a use case with fakes exercises the plumbing rather than
the patterns.
"""
import unittest
from dataclasses import FrozenInstanceError

from app.infrastructure.security.secret_scanner import (
    looks_like_env_file,
    scan_and_redact,
)

REDACTED = "[REDACTED_SECRET]"


class MustRedactTest(unittest.TestCase):
    CASES = [
        ("aws access key", "AKIAIOSFODNN7EXAMPLE"),
        ("aws secret", "aws_secret_access_key = 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'"),
        ("aws secret uppercase key name", "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
        ("google api key", "AIzaSyD-1234567890abcdefghijklmnopqrstu"),
        ("github classic pat", "ghp_" + "a" * 36),
        ("github oauth token", "gho_" + "b" * 36),
        ("github user token", "ghu_" + "c" * 36),
        ("github server token", "ghs_" + "d" * 36),
        ("github refresh token", "ghr_" + "e" * 36),
        ("bearer header", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123"),
        ("bearer lowercase", "authorization: bearer abcdefghijklmnopqrstuvwxyz123"),
        ("slack bot token", "xoxb-123456789012-abcdefghijkl"),
        ("slack user token", "xoxp-123456789012-abcdefghijkl"),
        ("api_key assignment", 'api_key = "abcdefghijklmnop1234"'),
        ("API-KEY assignment", 'API-KEY: "abcdefghijklmnop1234"'),
        ("client secret", "client_secret = 'abcdefghijklmnop1234'"),
        ("access token assignment", 'access_token = "abcdefghijklmnop1234"'),
        ("password assignment", "password = 'correcthorsebatterystaple1'"),
        ("token assignment", 'token: "abcdefghijklmnopqrstuvwxyz"'),
    ]

    def test_every_credential_shape_is_redacted(self):
        for label, secret in self.CASES:
            with self.subTest(label=label):
                result = scan_and_redact(f"before\n{secret}\nafter")
                self.assertTrue(result.had_findings, f"{label} was not detected")
                self.assertIn(REDACTED, result.redacted_content)
                self.assertNotIn(secret, result.redacted_content)

    def test_a_private_key_block_is_removed_whole(self):
        content = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA1234567890\nabcdefghijklmnop\n"
            "-----END RSA PRIVATE KEY-----"
        )
        result = scan_and_redact(f"x = '''\n{content}\n'''")
        self.assertNotIn("MIIEowIBAAKCAQEA", result.redacted_content)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", result.redacted_content)

    def test_several_secrets_are_all_counted(self):
        content = f"AKIAIOSFODNN7EXAMPLE\nghp_{'a' * 36}\nAIzaSyD-1234567890abcdefghijklmnopqrstu"
        result = scan_and_redact(content)
        self.assertEqual(result.findings_count, 3)

    def test_the_surrounding_code_survives(self):
        result = scan_and_redact('def connect():\n    api_key = "abcdefghijklmnop1234"\n    return api_key')
        self.assertIn("def connect():", result.redacted_content)
        self.assertIn("return api_key", result.redacted_content)


class MustNotRedactTest(unittest.TestCase):
    """
    Over-redaction is its own failure: it silently deletes the code an
    answer was supposed to explain.
    """

    CASES = [
        ("short variable", "key = 'abc'"),
        ("ordinary assignment", "name = 'widget-service'"),
        ("a function called get_token", "def get_token(self):\n    return self._token"),
        ("reading a key from env", 'api_key = os.getenv("API_KEY")'),
        ("prose about tokens", "The access token is refreshed every hour by the scheduler."),
        ("a url", "https://github.com/acme/widgets"),
        ("import line", "from app.security import token_utils"),
        ("aws-like but too short", "AKIA123"),
        ("google-like but too short", "AIzaShort"),
        ("bearer without a token", "Bearer "),
        ("a docstring mentioning password", '"""Checks the password strength."""'),
    ]

    def test_a_placeholder_is_redacted_which_is_the_safe_direction(self):
        # "your_api_key_here" is 17 characters assigned to api_key, so it
        # matches. Redacting a placeholder costs nothing; the pattern that
        # would spare it would also spare a real short key, so the
        # conservative behaviour is the right one and is pinned here.
        result = scan_and_redact('api_key = "your_api_key_here"')
        self.assertTrue(result.had_findings)

    def test_ordinary_code_is_left_alone(self):
        for label, content in self.CASES:
            with self.subTest(label=label):
                result = scan_and_redact(content)
                self.assertFalse(result.had_findings, f"{label} was redacted but should not be")
                self.assertEqual(result.redacted_content, content)


class EdgeCaseTest(unittest.TestCase):
    def test_empty_content_is_handled(self):
        result = scan_and_redact("")
        self.assertFalse(result.had_findings)
        self.assertEqual(result.findings_count, 0)

    def test_result_is_immutable(self):
        result = scan_and_redact("AKIAIOSFODNN7EXAMPLE")
        with self.assertRaises(FrozenInstanceError):
            result.findings_count = 0  # type: ignore[misc]

    def test_redaction_is_idempotent(self):
        once = scan_and_redact("AKIAIOSFODNN7EXAMPLE").redacted_content
        twice = scan_and_redact(once)
        self.assertFalse(twice.had_findings, "redacting an already-redacted string found more")


class EnvFileTest(unittest.TestCase):
    """
    A .env is all secrets, so it is skipped entirely rather than redacted:
    there is no useful structure left afterwards anyway.
    """

    def test_env_files_are_recognised(self):
        for path in [".env", "backend/.env", ".env.local", ".env.production", "a/b/.env.staging"]:
            with self.subTest(path=path):
                self.assertTrue(looks_like_env_file(path))

    def test_the_example_file_is_not_skipped(self):
        # .env.example is committed on purpose and contains no real values.
        self.assertFalse(looks_like_env_file(".env.example"))

    def test_ordinary_files_are_not_mistaken_for_env_files(self):
        for path in ["app/main.py", "README.md", "environment.yml", "docs/env.md", "env"]:
            with self.subTest(path=path):
                self.assertFalse(looks_like_env_file(path))
