"""
DetectAgentToolingUseCase — what AI agent tooling a repository ships.

Repositories increasingly carry configuration for coding agents: Claude
skills and subagents, project instructions, Cursor rules, MCP server
declarations, GPT plugin manifests. Nothing currently tells you what is
in there without going and looking, and the useful question is not only
"what does this repo have" but "which of these could I take and use, and
what would it do for me".

No model is involved. This is path matching over the manifest the ingest
already produced plus a frontmatter read, so it costs nothing, needs no
GitHub call, and returns the same answer every time.

A deliberate note on trust. Every other file this app reads is code, and
code is inert. These files are prose written to be obeyed by an agent,
which makes them the one input in the system designed to manipulate the
thing reading it. Nothing here interprets them: the name and description
are lifted verbatim from frontmatter and handed onward as data. If a
prose layer is ever added on top, it must delimit this content the way
ingest already delimits a README, and for a stronger reason.
"""
from __future__ import annotations

import logging
import re

from app.application.use_cases.ingest_repo import RepoCache
from app.domain.entities import AgentAsset, AgentToolingReport, RepoManifest

logger = logging.getLogger(__name__)


class RepoNotIngestedError(Exception):
    """Raised when the repo has not been ingested at this commit."""


# Kinds, in the order a reader most likely cares about. Each carries a
# label and what having it would actually do for you, because "this repo
# has a .cursor directory" is not information.
KIND_LABEL = {
    "claude_skill": "Claude skill",
    "claude_subagent": "Claude subagent",
    "claude_command": "Claude slash command",
    "project_instructions": "Agent project instructions",
    "cursor_rule": "Cursor rule",
    "mcp_server": "MCP server declaration",
    "gpt_plugin": "GPT plugin manifest",
    "llms_txt": "LLM guide",
}

KIND_USE = {
    "claude_skill": (
        "Copy the folder into .claude/skills/ in your own project and Claude Code "
        "will load it when the work matches its description."
    ),
    "claude_subagent": (
        "Copy into .claude/agents/ to get a specialised agent you can hand a task to, "
        "with its own tools and its own context."
    ),
    "claude_command": (
        "Copy into .claude/commands/ and it becomes a /slash-command in Claude Code."
    ),
    "project_instructions": (
        "Read it to learn the conventions this project expects. Agents working in this "
        "repository are told to follow it, so it is the fastest way to see the house rules."
    ),
    "cursor_rule": (
        "Copy into .cursor/rules/ to apply the same guidance in Cursor."
    ),
    "mcp_server": (
        "Lists the MCP servers this project connects to. The same file works in any MCP "
        "client, so it shows what external tools the project expects to reach."
    ),
    "gpt_plugin": (
        "Declares this project as a tool a GPT can call, and what it exposes."
    ),
    "llms_txt": (
        "A guide the project wrote for language models reading it. Often the most direct "
        "summary of the codebase that exists."
    ),
}

# Order matters: the first pattern that matches a path wins, so the more
# specific directory forms are listed ahead of the bare filenames.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Both layouts, because they are different things: .claude/skills/ is
    # where a skill is installed, skills/ is where a repo that ships them
    # keeps them. The leading (^|/) means one pattern covers both, at any
    # depth, which also catches plugins/<x>/skills/<y>/SKILL.md.
    ("claude_skill", re.compile(r"(^|/)skills/[^/]+/skill\.md$", re.I)),
    ("claude_subagent", re.compile(r"(^|/)\.claude/agents/[^/]+\.md$", re.I)),
    ("claude_command", re.compile(r"(^|/)\.claude/commands/[^/]+\.md$", re.I)),
    ("cursor_rule", re.compile(r"(^|/)\.cursor/rules/[^/]+\.(mdc|md)$", re.I)),
    ("gpt_plugin", re.compile(r"(^|/)(\.well-known/)?ai-plugin\.json$", re.I)),
    ("mcp_server", re.compile(r"(^|/)\.?mcp\.json$", re.I)),
    ("llms_txt", re.compile(r"(^|/)llms(-full)?\.txt$", re.I)),
    ("project_instructions", re.compile(
        r"(^|/)("
        r"claude\.md|agents\.md|agent\.md|"
        r"\.cursorrules|"
        r"\.github/copilot-instructions\.md|"
        r"\.windsurfrules|\.clinerules"
        r")$", re.I)),
]

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.S)
_MAX_EXCERPT = 400
_MAX_FIELD = 300


def _classify(path: str, fields: dict[str, str] | None = None) -> str | None:
    for kind, pattern in _PATTERNS:
        if pattern.search(path):
            return kind
    # A file the spec names SKILL.md, declaring both the fields the spec
    # requires, is a skill wherever it happens to sit. Real repositories
    # put them in places no path rule anticipates, and the frontmatter is
    # a stronger signal than the directory above it.
    if fields and path.rsplit("/", 1)[-1].lower() == "skill.md":
        if fields.get("name") and fields.get("description"):
            return "claude_skill"
    return None


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """
    Read the leading YAML block as flat key/value pairs.

    Deliberately not a YAML parser: these files are untrusted, a real
    parser is a much larger attack surface than this needs, and every
    field worth reading here is a plain scalar on one line. Anything
    nested is simply not returned.
    """
    match = _FRONTMATTER.match(content)
    if not match:
        return {}, content

    fields: dict[str, str] = {}
    lines = match.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith((" ", "\t")) or ":" not in line:
            continue  # part of a nested value, or not a pair
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()

        # Block scalars. Real skills write long descriptions as "> " or
        # "|-" and continue on the indented lines below, so reading the
        # text after the colon yields the marker instead of the text.
        # Only an explicit marker counts: a bare "key:" followed by
        # indented lines is a nested mapping, which stays ignored.
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            folded = value.startswith(">")
            block: list[str] = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            value = (" ".join(b for b in block if b) if folded else "\n".join(block)).strip()
        else:
            value = value.strip("\"'")

        if key and value:
            fields[key] = value[:_MAX_FIELD]
    return fields, content[match.end():]


def _fallback_name(kind: str, path: str) -> str:
    parts = path.split("/")
    if kind == "claude_skill" and len(parts) >= 2:
        return parts[-2]  # .claude/skills/<this>/SKILL.md
    return parts[-1]


class DetectAgentToolingUseCase:
    def __init__(self, *, cache: RepoCache) -> None:
        self._cache = cache

    async def execute(self, *, repo_full_name: str, commit_sha: str) -> AgentToolingReport:
        manifest = await self._cache.get(repo_full_name, commit_sha)
        if manifest is None:
            raise RepoNotIngestedError(
                f"{repo_full_name} has not been ingested at {commit_sha[:7]}."
            )
        return AgentToolingReport(
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
            assets=self._collect(manifest),
        )

    def _collect(self, manifest: RepoManifest) -> list[AgentAsset]:
        assets: list[AgentAsset] = []
        for node in manifest.files:
            content = node.content or ""
            fields, body = _parse_frontmatter(content)
            kind = _classify(node.path, fields)
            if kind is None:
                continue

            name = fields.get("name") or _fallback_name(kind, node.path)
            description = fields.get("description", "")

            if not description:
                # No frontmatter: the first real line of prose is the
                # closest thing the file offers to a summary.
                for line in body.splitlines():
                    stripped = line.strip().lstrip("#").strip()
                    if stripped:
                        description = stripped[:_MAX_FIELD]
                        break

            assets.append(AgentAsset(
                kind=kind,
                path=node.path,
                name=name[:_MAX_FIELD],
                description=description,
                body_excerpt=body.strip()[:_MAX_EXCERPT],
                has_frontmatter=bool(fields),
                size_bytes=node.size_bytes,
            ))

        # Grouped by kind in the order above, then by path, so the list
        # reads the same way every time.
        order = {kind: i for i, (kind, _) in enumerate(_PATTERNS)}
        assets.sort(key=lambda a: (order.get(a.kind, 99), a.path.lower()))
        return assets
