"""
GenerateHandbookUseCase — the whole repository, as one document.

This runs no analysis of its own. The reading order comes from the
onboarding agent, the maintainability findings from the code health
checks, and the stack and file counts from the cached manifest. Its only
job is to gather them under one commit so that a handbook and the screens
it came from can never disagree.

The health report is best-effort on purpose. It needs a parsed index, and
a repository that failed to parse still has a manifest and a guide worth
writing down; losing the whole document over a missing section would be
the wrong trade. The guide is not optional: a handbook with no reading
order is not a handbook.
"""
from __future__ import annotations

import logging

from app.application.use_cases.analyze_code_health import (
    AnalyzeCodeHealthUseCase,
    RepoNotIndexedError,
)
from app.application.use_cases.generate_onboarding import (
    GenerateOnboardingUseCase,
    RepoNotIngestedError,
)
from app.application.use_cases.ingest_repo import RepoCache
from app.domain.entities import Handbook

logger = logging.getLogger(__name__)


class GenerateHandbookUseCase:
    def __init__(
        self,
        *,
        onboarding_use_case: GenerateOnboardingUseCase,
        code_health_use_case: AnalyzeCodeHealthUseCase,
        cache: RepoCache,
    ) -> None:
        self._onboarding = onboarding_use_case
        self._code_health = code_health_use_case
        self._cache = cache

    async def execute(self, *, repo_full_name: str, commit_sha: str) -> Handbook:
        manifest = await self._cache.get(repo_full_name, commit_sha)
        if manifest is None:
            raise RepoNotIngestedError(
                f"{repo_full_name} has not been ingested at {commit_sha[:7]}. "
                "Ingest it first, then build the handbook."
            )

        # Propagates: without a reading order there is no handbook.
        guide = await self._onboarding.execute(
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
        )

        health = None
        try:
            health = await self._code_health.execute(
                repo_full_name=repo_full_name,
                commit_sha=commit_sha,
            )
        except RepoNotIndexedError:
            logger.info(
                "No parsed index for %s at %s; handbook written without the "
                "maintainability section.",
                repo_full_name,
                commit_sha[:7],
            )
        except Exception:
            # Any other failure in a supporting section is logged and
            # dropped rather than losing the document.
            logger.exception("Code health failed while building the handbook for %s", repo_full_name)

        return Handbook(
            repo_full_name=repo_full_name,
            commit_sha=commit_sha,
            guide=guide,
            detected_stack=list(manifest.detected_stack),
            file_count=manifest.file_count,
            skipped_file_count=manifest.skipped_file_count,
            health=health,
        )
