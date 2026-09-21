# Repo Pilot

A codebase onboarding agent. Point it at a public GitHub repository and it:

- **Explores** — fetches the tree, detects the stack, chunks and embeds the code, and writes a one-paragraph summary.
- **Explains** — answers "how does X work?" by vector-searching the code, tracing the call graph with tree-sitter, and citing the real files it used. Citations come from retrieval, never from the model's text.
- **Diagrams** — draws the import graph as a Mermaid flowchart. The structure is parsed, not generated: the LLM cannot add or remove an edge. Click any node to read that file's source.
- **Onboards** — ranks the repo's most central files from the import graph and lays out a reading order, so a newcomer knows where to start.
- **Traces impact** — answers "what breaks if I change this?" by walking the dependency graph backwards from a file or symbol.
- **Grades code health** — a maintainability report card (circular imports, hub modules, untested modules, long functions, large files, unreferenced symbols) computed entirely from the parsed index, with no LLM involved.

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

**Personas.** Every answer can be written for a new developer (the default: defines terms, explains the why, walks the flow in order), a senior engineer (terse, mechanism first, names patterns and flags smells) or a product manager (behaviour and user impact, no code). Only the audience paragraph of the prompt changes; retrieval, tracing, citations and confidence are identical across personas, so the three answers rest on the same evidence.

**Deep investigation mode** runs up to `DEEP_INVESTIGATION_MAX_STEPS` (default 5) search rounds before answering. After each round the LLM is asked whether the evidence is sufficient and, if not, for one short search phrase to try next; that phrase is embedded and searched exactly like a question. It is the only thing the model steers — it never names files, and citations still come solely from retrieval and tree-sitter traces. The loop stops early when the planner is satisfied, repeats a search, or a search adds nothing new, and the UI shows the trail it followed. Cost: at most `max_steps` small planner calls plus one synthesis call, and it has its own tighter per-IP rate limit.

**Onboarding** ("learn this codebase") answers "where do I start?". Entry points come from three independent signals — what ingestion detected, recognised bootstrap/config basenames, and graph roots (files that import others but nothing imports). Importance is a centrality score per file: in-degree dominates, with smaller contributions from wiring breadth, distance from an entry point, and how many symbols the file defines — never line count, so a vendored blob does not outrank the module everything imports. Entry points lead the reading order, then the highest-scoring files, 5-8 in total. One bounded LLM call turns those already-chosen files into prose; it cannot add a file to the list, and if it fails the guide still renders from graph-derived text.

**Live investigation** streams a deep investigation over SSE (`GET /api/ask/stream`) so the trail appears as it happens rather than all at once at the end. The events describe what the system *did* — searched, traced, inspected, verified, completed — and deliberately carry no prompts, no system messages and no model deliberation. The investigation runs as a task feeding a queue, so the stream keeps draining while a slow LLM call is in flight, and a disconnected browser cancels the work rather than burning further calls. Quick mode still uses the plain `POST /api/ask`, which is unchanged.

**Impact analysis** ("what breaks if I change X?") is a reverse breadth-first walk of the cached import graph: depth 1 is a direct dependent, deeper is indirect, bounded by depth and node caps so a cyclic graph cannot run away. Picking a symbol additionally searches the parsed call graph for code that names it, ranked so same-file and real-importer matches lead — name-based tracing is not scope-aware. No LLM decides membership; it may only restate the finished counts, and falls back to a computed sentence when unavailable. The result highlights the blast radius on the diagram without adding or removing a single node or edge.

**Code health** is a report card computed purely from the cached index — no LLM call, no network. Six checks: circular imports (Tarjan's strongly-connected components over the module graph), hub modules (high fan-in), untested modules (source files no test file imports, ranked by how many modules depend on them), long functions and large files (line thresholds), and unreferenced symbols (functions no other code calls or names — reported only when the name is unique in the repo, and after checking the raw text for JSX tags, imports, and other non-call references). The score starts at 100 and each finding deducts by severity, capped per check so one category cannot zero it; it is labelled a heuristic in the UI. Every finding links to the file drawer, so a finding leads to the source, then to "ask about this file" or impact analysis.

**Diagrams** are built from the cached module edges. Import graphs are extracted for Python, JavaScript/TypeScript (including `.vue`/`.svelte`), Dart, Go, Rust, Ruby, Java, and PHP, plus Python-to-template and template-to-static-asset links.

The Mermaid output is laid out as a nested folder tree: real nesting (`frontend` ⊃ `src` ⊃ `pages`), single-child folder chains merged into one box, and a lone file hoisted into its parent with a relative label. When most files in a folder share a dependency, one arrow is drawn from the folder instead of one per file (labelled `3/4` when it is most-but-not-all), which is what keeps hub modules like an API client from receiving a line from every page. Repos too large to draw at file level are aggregated to folder-level nodes with edges weighted by how many imports they collapse; the UI says when this has happened.

The viewer opens at a readable zoom, and has `+`/`−`, "fit" (whole-diagram overview), "full" (expand to the window, Esc to exit), and PNG/SVG export.

## Tests

```bash
python -m unittest discover -s tests -t .   # backend
npm install && npm test                     # frontend (jsdom, dev-only dependency)
```

The frontend tests load the real `app.html`, `app.js` and `features.js` into jsdom
rather than a fixture, so they fail if the page and the scripts drift apart. The app
itself still has no build step and no runtime JS dependencies.


## Authors

Built by Rameen Fatima and Dania Imran.

## Limits worth knowing

- Public repositories only, and only via `https://github.com/owner/repo` URLs (this is also the SSRF guard).
- Call tracing is name-based, not scope-aware. Same-file matches are preferred, then imported files, then anywhere — see the note at the top of `tree_sitter_parser.py`.
- Free-tier LLM providers meter tokens per minute (Groq: 8k/min on `gpt-oss-120b`). Prompts are budgeted to fit one deep investigation inside that, but asking several deep questions back to back can still return "the AI service may be at capacity" — wait a minute or use quick mode.

## License

MIT — see [LICENSE](LICENSE).
