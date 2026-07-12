"""
Value objects: small, self-validating, immutable pieces of data.
No framework imports here — pure Python only. If this file ever needs
to import FastAPI, Pydantic, or a DB client, it's in the wrong layer.
"""
from dataclasses import dataclass
import re
from enum import Enum


# Matches https://github.com/owner/repo (optionally with a trailing
# slash or .git). Anything else gets rejected at construction time —
# this is our SSRF guard, enforced at the domain boundary.
_GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class RepoUrl:
    """A validated GitHub repo URL. If you have one of these, it's safe to use."""
    raw: str

    def __post_init__(self):
        match = _GITHUB_URL_PATTERN.match(self.raw.strip())
        if not match:
            raise ValueError(
                f"Invalid GitHub repo URL: {self.raw!r}. "
                "Only https://github.com/owner/repo URLs are allowed."
            )
        # store the parsed parts via object.__setattr__ since the dataclass is frozen
        object.__setattr__(self, "_owner", match.group(1))
        object.__setattr__(self, "_repo", match.group(2))

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def repo(self) -> str:
        return self._repo

    @property
    def normalized(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


@dataclass(frozen=True)
class Citation:
    """Points to where in the code an answer came from."""
    file_path: str
    start_line: int
    end_line: int

    def __post_init__(self):
        if self.start_line < 1 or self.end_line < 1:
            raise ValueError("Line numbers must be >= 1")
        if self.start_line > self.end_line:
            raise ValueError("start_line cannot be after end_line")

    def __str__(self) -> str:
        return f"{self.file_path}:{self.start_line}-{self.end_line}"


@dataclass(frozen=True)
class ConfidenceScore:
    """
    How confident the Explainer Agent is in a quick-mode answer.
    Used to decide whether to escalate to deep investigation mode.
    """
    value: float

    def __post_init__(self):
        if not (0.0 <= self.value <= 1.0):
            raise ValueError(f"Confidence must be between 0 and 1, got {self.value}")

    def is_low(self, threshold: float = 0.5) -> bool:
        return self.value < threshold


class QuestionMode(str, Enum):
    QUICK = "quick"
    DEEP = "deep"