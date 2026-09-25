"""
Scans file content for likely secrets (API keys, tokens, private keys)
BEFORE that content is ever embedded, stored, or sent to an LLM.

This is a redaction pass, not a blocker: we don't reject the whole file
just because it contains what looks like a secret (that would break
ingestion on any repo with a .env.example or a test fixture full of
fake keys). Instead we replace the matched substring with a placeholder,
so the LLM/embedding model never sees the real value, but the
surrounding code structure is still useful for tracing/explaining.

This is deliberately regex-based and conservative (may over-redact).
Over-redaction is the safe failure mode here — false positives cost us
a placeholder string in an explanation; false negatives could leak a
real credential into an LLM prompt or a public vector store.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ScanResult:
    redacted_content: str
    findings_count: int
    had_findings: bool


# Each pattern: (name, compiled regex). Patterns match the SECRET VALUE
# itself, not just a key name, so we don't need context lines.
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret_key", re.compile(r"(?i)aws_secret_access_key\s*=\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("generic_bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key_block", re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?-----END[ A-Z]*PRIVATE KEY-----")),
    ("generic_api_key_assignment", re.compile(
        r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret)"
        r"\s*[:=]\s*['\"][A-Za-z0-9_\-./+]{16,}['\"]"
    )),
    # High-entropy-looking long base64/hex strings assigned to a variable
    # named like a credential — catches custom/unknown token formats.
    ("suspicious_long_assignment", re.compile(
        r"(?i)(token|secret|password|passwd|pwd)\s*[:=]\s*['\"][A-Za-z0-9_\-./+]{20,}['\"]"
    )),
]


def scan_and_redact(content: str) -> ScanResult:
    """
    Returns redacted content with every match replaced by
    '[REDACTED_SECRET]', plus a count of how many redactions happened.
    """
    if not content:
        return ScanResult(redacted_content=content, findings_count=0, had_findings=False)

    redacted = content
    total_findings = 0

    for _name, pattern in _SECRET_PATTERNS:
        redacted, count = pattern.subn("[REDACTED_SECRET]", redacted)
        if count:
            total_findings += count

    return ScanResult(
        redacted_content=redacted,
        findings_count=total_findings,
        had_findings=total_findings > 0,
    )


def looks_like_env_file(file_path: str) -> bool:
    """
    .env files get extra caution: skip embedding entirely rather than
    redact-and-include, since a .env is ALL secrets by definition and
    there's no useful "code structure" left after redaction anyway.
    """
    name = file_path.rsplit("/", 1)[-1].lower()
    return name == ".env" or name.startswith(".env.") and name != ".env.example"
