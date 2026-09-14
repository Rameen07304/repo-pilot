# Repo Pilot

A codebase onboarding agent. Point it at a public GitHub repository and it:

- **Explores** — fetches the tree, detects the stack, chunks and embeds the code, and writes a one-paragraph summary.
- **Explains** — answers "how does X work?" by vector-searching the code, tracing the call graph with tree-sitter, and citing the real files it used. Citations come from retrieval, never from the model's text.
- **Diagrams** — draws the import graph as a Mermaid flowchart. The structure is parsed, not generated: the LLM cannot add or remove an edge.

## Quick start

Requires Python 3.10+.

```bash
python -m venv venv
venv/Scripts/activate        # Windows
# source venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
cp .env.example .env         # then fill in GEMINI_API_KEY, GROQ_API_KEY, and (recommended) GITHUB_TOKEN
python -m uvicorn app.interface.main:app --port 8000
```

Open http://localhost:8000. First start downloads the local embedding model (`all-MiniLM-L6-v2`, ~90 MB, one time).

`GITHUB_TOKEN` is optional. An ingest costs three GitHub API requests regardless of repo size (commit lookup, file tree, one archive download), so the unauthenticated limit of 60/hour is enough for casual use; a token raises it to 5000/hour.

## Configuration

Everything is read from `.env` at startup; see `.env.example` for the full list. The ones that matter most:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY`, `GROQ_API_KEY` | Both are required. `LLM_PRIMARY_PROVIDER` (`gemini` or `groq`) picks which one answers first; the other takes over if it fails. Embeddings are always computed locally, regardless. |
| `GITHUB_TOKEN` | Raises the GitHub API rate limit from 60 to 5000 requests/hour (about 20 vs 1600 ingests per hour). |
| `MAX_FILES_PER_REPO`, `MAX_TOTAL_REPO_SIZE_BYTES` | Ingestion caps. Repos over these are rejected up front rather than partially indexed. |
| `RATE_LIMIT_*_PER_HOUR_PER_IP` | Per-IP question limits, enforced before any LLM call is spent. |
| `TRUST_PROXY_HEADERS` | Set `true` only behind a reverse proxy you control; otherwise `X-Forwarded-For` is client-supplied and would let anyone bypass the per-IP limits. |

## How it works

```
app/
  domain/          entities and value objects — no dependencies on anything else
  application/     use cases (ingest, answer, diagram) + the Protocols they need
  infrastructure/  concrete adapters: GitHub API, Gemini/Groq, Chroma, SQLite, tree-sitter
  interface/       FastAPI routes, schemas, templates, static assets, composition root
```

Dependencies point inward: `interface -> application -> domain`, with `infrastructure` implementing the application layer's Protocols. `app/interface/dependencies.py` is the only file that imports concrete infrastructure classes.

**Ingestion** resolves the latest commit SHA and returns from cache if that exact commit was seen before. Otherwise it fetches the file tree, applies the size and noise filters (`node_modules`, lockfiles, binaries, platform scaffolding), downloads the commit as a single tarball and reads the wanted files from it in memory, redacts secrets, chunks each file (function/class level where a tree-sitter grammar exists, fixed windows otherwise), embeds the chunks into Chroma, builds a `ParsedRepoIndex` (symbols + module edges), and makes exactly one LLM call for the summary. Fetching a 100-file repo takes a few seconds; most of the remaining time is embedding on the CPU.

If any file could not be fetched, the result is returned with a warning and is **not** cached, so re-running the ingest retries rather than serving a partial index for that commit.

**Answering** embeds the question, searches only this repo+commit's chunks, maps the top hit to its enclosing symbol, traces calls from there (name-based, bounded hops), and makes one LLM call with the retrieved snippets. Confidence is the top similarity score.

**Diagrams** are built from the cached module edges. Import graphs are extracted for Python, JavaScript/TypeScript (including `.vue`/`.svelte`), Dart, Go, Rust, Ruby, Java, and PHP, plus Python-to-template and template-to-static-asset links.

The Mermaid output is laid out as a nested folder tree: real nesting (`frontend` ⊃ `src` ⊃ `pages`), single-child folder chains merged into one box, and a lone file hoisted into its parent with a relative label. When most files in a folder share a dependency, one arrow is drawn from the folder instead of one per file (labelled `3/4` when it is most-but-not-all), which is what keeps hub modules like an API client from receiving a line from every page. Repos too large to draw at file level are aggregated to folder-level nodes with edges weighted by how many imports they collapse; the UI says when this has happened.

The viewer opens at a readable zoom, and has `+`/`−`, "fit" (whole-diagram overview), "full" (expand to the window, Esc to exit), and PNG/SVG export.

## Tests

```bash
python -m unittest discover -s tests -t .
```

`check_setup.py` is an additional dependency-free smoke test of the domain and parsing layers.

## Limits worth knowing

- Public repositories only, and only via `https://github.com/owner/repo` URLs (this is also the SSRF guard).
- Call tracing is name-based, not scope-aware. Same-file matches are preferred, then imported files, then anywhere — see the note at the top of `tree_sitter_parser.py`.
- The "deep investigation" mode in the UI is not implemented yet; every question runs in quick mode.
