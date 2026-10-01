"""
GET /proof — the claim, demonstrated on a real repository.

Every tool in this category says its output is accurate. Saying it is
free, so saying it persuades nobody. This page instead makes three
claims that a reader can check without trusting anything:

  1. Every arrow carries the line it came from. Shown as a count, with
     links that open the file at that line on the pinned commit.
  2. The diagram declares what it could not see. Shown as the real
     coverage figures, including the ones that are unflattering.
  3. The same commit always draws the same diagram. Shown by rendering
     it twice and printing both SHA-256 digests.

The third is the one that cannot be imitated by a tool that asks a model
to draw the picture, and it is the cheapest to verify: the two digests
are either identical on the page or they are not.

It runs on whatever repository the reader most recently ingested, not on
a name baked into a template, because a demonstration on someone else's
codebase is a screenshot and a demonstration on your own is evidence.
"""
from __future__ import annotations

import hashlib
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates

from app.application.use_cases.export_architecture import RepoNotExportableError
from app.interface.architecture_svg import render_architecture_svg

logger = logging.getLogger(__name__)
router = APIRouter()


def _digest(markup: str) -> str:
    return hashlib.sha256(markup.encode("utf-8")).hexdigest()


@router.get("/proof")
async def proof(
    request: Request,
    repo: str | None = Query(default=None, max_length=200),
):
    container = request.app.state.container
    templates: Jinja2Templates = request.app.state.templates

    ctx: dict = {"static_version": request.app.state.static_version, "repo": None}

    names = await container.cache.list_repos()
    chosen = repo if repo in names else (names[0] if names else None)
    if chosen is None:
        # Nothing ingested yet. The page says so rather than inventing a
        # demonstration, because a fabricated proof is worse than none.
        return templates.TemplateResponse(request, "proof.html", ctx)

    commits = await container.cache.list_commits(chosen)
    if not commits:
        return templates.TemplateResponse(request, "proof.html", ctx)

    try:
        export = await container.architecture_export_use_case.execute(
            repo_full_name=chosen, commit_sha=commits[0].commit_sha
        )
    except RepoNotExportableError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # Rendered twice on purpose: the two digests ARE the determinism
    # claim. Computing it once and asserting the property would be the
    # same empty promise every other tool makes.
    first, second = render_architecture_svg(export), render_architecture_svg(export)

    evidenced = export.evidenced_edge_count
    total = len(export.edges)
    samples = [e for e in export.edges if e.lines][:3]

    ctx.update(
        {
            "repo": chosen,
            "others": [n for n in names if n != chosen][:6],
            "commit_sha": export.commit_sha,
            "short_sha": export.commit_sha[:7],
            "edges_total": total,
            "edges_evidenced": evidenced,
            "every_edge_cited": total > 0 and evidenced == total,
            "coverage": export.coverage,
            "samples": samples,
            "digest_a": _digest(first),
            "digest_b": _digest(second),
            "digests_match": first == second,
            "svg_bytes": len(first),
            "nodes_drawn": export.coverage.nodes_drawn,
            "nodes_total": export.coverage.nodes_total,
        }
    )
    return templates.TemplateResponse(request, "proof.html", ctx)
