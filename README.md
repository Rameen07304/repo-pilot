---
title: Repo Pilot
emoji: 🤖
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Read an unfamiliar codebase. Every claim cites a file and line.
---

# Repo Pilot

A codebase onboarding agent. Point it at a public GitHub repository and it:

- **Explores** — fetches the tree, detects the stack, chunks and embeds the code, and writes a one-paragraph summary.
- **Explains** — answers "how does X work?" by vector-searching the code, tracing the call graph with tree-sitter, and citing the real files it used. Citations come from retrieval, never from the model's text.
- **Onboards** — ranks the repo's most central files from the import graph and lays out a reading order, so a newcomer knows where to start.
- **Diagrams four ways** — the import graph, what changed between two commits, what one function calls, and the branches inside it. All four are parsed, not generated.
- **Answers a question as a diagram** — ask "how does login work" and get the call flow. A model picks only *where to start*; every step after that comes from the parse.
- **Explains a pull request** — paste its URL and see what it changes structurally, compared against the merge base.
- **Traces impact** — answers "what breaks if I change this?" by walking the dependency graph backwards from a file or symbol.
- **Grades code health** — a maintainability report card computed entirely from the parsed index, with no LLM involved.
- **Finds the AI tooling a repo ships** — `SKILL.md` files and agent configs, with what each one does.
- **Exports documents that stand alone** — self-contained HTML with no network dependency, where every arrow links to the line it was resolved from, and the page states what the parse could not see.

The through-line: **a language model never decides what is true about your code.** It writes prose about files the parser already found, and picks a starting point from a list the search already produced. It cannot add a file, an edge, or a step.

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
| `GITHUB_TOKEN` | Raises the GitHub API rate limit from 60 to 5000 requests/hour (about 20 vs 1600 ingests per hour). Scope it to **public repositories, read-only**: private repos are refused during ingest, and a token that cannot read them means a missed check could never expose one either. |
| `MAX_FILES_PER_REPO`, `MAX_TOTAL_REPO_SIZE_BYTES` | Ingestion caps. Repos over these are rejected up front rather than partially indexed. |
| `RATE_LIMIT_*_PER_HOUR_PER_IP` | Per-IP question limits, enforced before any LLM call is spent. |
| `RATE_LIMIT_LLM_CALLS_PER_DAY_PER_IP` | Each caller's share of the day's model budget, so the first visitor cannot spend all of it. |
| `MAX_CACHED_REPOS` | How many repositories the store keeps, oldest evicted first. `0` disables the cap for local use. |
| `RATE_LIMIT_DOCUMENTS_PER_HOUR_PER_IP` | A separate, larger allowance for the generated documents. They read the cached parse and run no model, so browsing them must not spend an hour's questions. |
| `ARCH_EXPORT_MAX_NODES`, `ARCH_EXPORT_MAX_EDGES` | How much of the architecture export is **drawn**. The evidence table always lists every dependency; these only keep the picture legible. |
| `SEQUENCE_MAX_DEPTH`, `SEQUENCE_MAX_STEPS` | How far a call sequence follows and how many steps it draws. Small on purpose: a Flutter screen traced 123 calls into a 5,600px-tall wall nobody could read. |
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

**Personas.** Every answer can be written for a new developer (the default: defines terms, explains the why, walks the flow in order), a senior engineer (terse, mechanism first, names patterns and flags smells), a product manager (behaviour and user impact, no code), or someone exploring the repo for ideas (the techniques and patterns worth borrowing). Only the audience paragraph of the prompt changes; retrieval, tracing, citations and confidence are identical across personas, so the three answers rest on the same evidence.

**Conversation memory.** Follow-up questions work: "and where is that validated?" or "can the same person do it twice?" resolve against earlier turns. The thread lives in the page and is sent with each request, never stored server-side, so a reload or a different repo starts fresh. History does two things — it is prefixed to the text that gets embedded (a follow-up alone retrieves nothing useful) and it is passed to the model as a clearly delimited block it may use only to work out what the question refers to. Citations still come from this request's own search, so a file mentioned earlier cannot leak into a later answer's citations.

**Deep investigation mode** runs up to `DEEP_INVESTIGATION_MAX_STEPS` (default 5) search rounds before answering. After each round the LLM is asked whether the evidence is sufficient and, if not, for one short search phrase to try next; that phrase is embedded and searched exactly like a question. It is the only thing the model steers — it never names files, and citations still come solely from retrieval and tree-sitter traces. The loop stops early when the planner is satisfied, repeats a search, or a search adds nothing new, and the UI shows the trail it followed. Cost: at most `max_steps` small planner calls plus one synthesis call, and it has its own tighter per-IP rate limit.

**Onboarding** ("learn this codebase") answers "where do I start?". Entry points come from three independent signals — what ingestion detected, recognised bootstrap/config basenames, and graph roots (files that import others but nothing imports). Importance is a centrality score per file: in-degree dominates, with smaller contributions from wiring breadth, distance from an entry point, and how many symbols the file defines — never line count, so a vendored blob does not outrank the module everything imports. Entry points lead the reading order, then the highest-scoring files, 5-8 in total. One bounded LLM call turns those already-chosen files into prose; it cannot add a file to the list, and if it fails the guide still renders from graph-derived text.

**Live investigation** streams a deep investigation over SSE (`GET /api/ask/stream`) so the trail appears as it happens rather than all at once at the end. The events describe what the system *did* — searched, traced, inspected, verified, completed — and deliberately carry no prompts, no system messages and no model deliberation. The investigation runs as a task feeding a queue, so the stream keeps draining while a slow LLM call is in flight, and a disconnected browser cancels the work rather than burning further calls. Quick mode still uses the plain `POST /api/ask`, which is unchanged.

**Impact analysis** ("what breaks if I change X?") is a reverse breadth-first walk of the cached import graph: depth 1 is a direct dependent, deeper is indirect, bounded by depth and node caps so a cyclic graph cannot run away. Picking a symbol additionally searches the parsed call graph for code that names it, ranked so same-file and real-importer matches lead — name-based tracing is not scope-aware. No LLM decides membership; it may only restate the finished counts, and falls back to a computed sentence when unavailable. The result highlights the blast radius on the diagram without adding or removing a single node or edge.

**Code health** is a report card computed purely from the cached index — no LLM call, no network. Six checks: circular imports (Tarjan's strongly-connected components over the module graph), hub modules (high fan-in), untested modules (source files no test file imports, ranked by how many modules depend on them), long functions and large files (line thresholds), and unreferenced symbols (functions no other code calls or names — reported only when the name is unique in the repo, and after checking the raw text for JSX tags, imports, and other non-call references). The score starts at 100 and each finding deducts by severity, capped per check so one category cannot zero it. The result leads with what the grade means in words, a next-step line naming the finding to start with, and a caveat that it measures structure and never runs the code; findings are grouped under severity headings that explain the band rather than a bare "low". `POST /api/code-health/report` renders the same report as a Markdown document for download. Every finding links to the file drawer, so a finding leads to the source, then to "ask about this file" or impact analysis.

**Diagrams** are built from the cached module edges. Import graphs are extracted for Python, JavaScript/TypeScript (including `.vue`/`.svelte`), Dart, Go, Rust, Ruby, Java, and PHP, plus Python-to-template and template-to-static-asset links.

The Mermaid output is laid out as a nested folder tree: real nesting (`frontend` ⊃ `src` ⊃ `pages`), single-child folder chains merged into one box, and a lone file hoisted into its parent with a relative label. When most files in a folder share a dependency, one arrow is drawn from the folder instead of one per file (labelled `3/4` when it is most-but-not-all), which is what keeps hub modules like an API client from receiving a line from every page. Repos too large to draw at file level are aggregated to folder-level nodes with edges weighted by how many imports they collapse; the UI says when this has happened.

The viewer opens at a readable zoom, and has `+`/`−`, "fit" (whole-diagram overview), "full" (expand to the window, Esc to exit), and PNG/SVG export. A **detail** control picks how much to draw — overview (the shallowest folder level that still shows structure), balanced, or everything that fits. The caps are for eyes, not cost: a 106-file Flutter app drew 65 nodes and 120 edges before, which is a wall rather than a diagram.

**Call sequences and flowcharts.** Two more views of one function, both read off the syntax tree:

- A **call sequence** puts each file on a lifeline and draws every call between them in source order, with activation bars showing which call happened *because of* which. Calls that leave the repository are counted and named rather than drawn — nobody can open a framework's internals to check them — and calls that match several definitions with nothing to choose between them are reported as ambiguous instead of pointed somewhere.
- A **flowchart** draws the branches inside one function: boxes, diamonds, a Yes and a No. An `if` in the file is a diamond on the chart, because tree-sitter already produces the shape a flowchart wants. Node types were read off real parse trees per language, which is the only way to get them right — Ruby calls its `if` an `if`, Rust an `if_expression`, and Dart exposes no condition field at all.

The picker ranks entry points by what the chosen view needs: a sequence wants the function that calls the most, a flowchart the one that branches the most, and a Flutter `build()` is the first of those and the worst of the second.

**A question, answered as a diagram.** Ask "how does login work" and get the call flow. The split is the whole design: the question is embedded and searched, which gives files; functions in those files that call something internal become a shortlist; one bounded model call picks **one** entry from that shortlist and says why. Everything after that is traced from the parse. The model can only answer with an index from the list — anything else falls back to search order — so the worst a bad call can do is start in the wrong place, and the diagram is still a true description of whatever it started from. The page says which decision was the model's, in those words.

**Architecture delta.** What changed structurally between two commits, or across a pull request. Renames are resolved before dependencies are compared, so a file that only moved produces one entry instead of every dependency it touches appearing twice; two files sharing a name that both move are deliberately *not* paired, because guessing would put a wrong arrow in a review. Paste a pull request URL and it reads the two commits itself, comparing against the merge base GitHub reports rather than the tip of the base branch — otherwise every unrelated commit landed while the PR was open gets blamed on it.

**Documents that stand alone.** The architecture, a delta, a sequence and a flowchart each export as one self-contained HTML file: no CDN, no font request, no script pointing anywhere. They open from a folder on a plane and are still complete. Two properties do the work:

- **Evidence on every edge, always.** Each arrow records the line it was resolved from and links to that file at that line on the pinned commit. The full table is plain HTML, so the proof survives with JavaScript switched off.
- **A declared blind spot.** Files the ingest never fetched, extensions no grammar reads, grammars that did not load, and anything dropped to keep the picture legible are all counted and printed. A complete export says so explicitly rather than letting silence imply it.

Capping the drawing never trims the evidence: `edges` keeps every dependency, `drawn_edges` is the subset on the canvas.

**Agent tooling detection** lists the `SKILL.md` files and agent configs a repository ships, with what each one does, read from path patterns and frontmatter. No model, no extra GitHub call, identical every run.

**The handbook** gathers the reading order, the stack, the file counts and the maintainability findings into one Markdown document a team can commit next to the code.

## Tests

```bash
python -m unittest discover -s tests -t .   # backend
npm install && npm test                     # frontend (jsdom, dev-only dependency)
```

The frontend tests load the real `app.html`, `app.js` and `features.js` into jsdom
rather than a fixture, so they fail if the page and the scripts drift apart. The app
itself still has no build step and no runtime JS dependencies.


## Deployment

The `Dockerfile` builds a self-contained image. It downloads the embedding
model at build time rather than on first request, so a cold container does
not make its first visitor wait out a model download.

```bash
docker build -t repo-pilot .
docker run -p 7860:7860 --env-file .env repo-pilot
```

It targets Hugging Face Spaces, which runs containers as uid 1000 on port
7860, but nothing is Spaces-specific: `PORT` is read at start, so the same
image runs on Fly, Render or locally.

### Hugging Face Spaces

The YAML block at the top of this file is what makes the Space build as a
Docker space — `sdk: docker` and `app_port: 7860`. Without it the Space
will not start, whatever the Dockerfile says.

1. Create a Space, **SDK: Docker**, and push this repository to it.
2. Add `GEMINI_API_KEY`, `GROQ_API_KEY` and `GITHUB_TOKEN` under
   **Settings → Variables and secrets** as **Secrets**, not Variables.
   Variables are visible to anyone who can see the Space.
3. Scope the GitHub token to **public repositories, read-only**. Private
   repos are refused during ingest, and a token that cannot read them
   means a missed check could never expose one either.

On the free tier the container filesystem resets when the Space restarts,
so `data/` does not survive. Nothing breaks — the cache is designed to be
rebuildable — but the first visitor after a restart waits out a fresh
ingest. Attach persistent storage if that matters, or accept it and let
the examples warm the cache.

Two things worth knowing before you scale it:

- **One worker, deliberately.** The rate limiter and the daily quota guard
  hold their counters in memory, so a second worker would silently double
  every limit. Running more than one means moving both behind Redis first.
- **`data/` must persist** to keep the cache across restarts. Losing it
  costs a re-ingest and nothing else — it is designed to be rebuildable.

## Authors

Built by Rameen Fatima and Dania Imran.

## Limits worth knowing

- Public repositories only, and only via `https://github.com/owner/repo` URLs (this is also the SSRF guard).
- Call tracing is name-based, not scope-aware. Same-file matches are preferred, then imported files, then anywhere — see the note at the top of `tree_sitter_parser.py`.
- A flowchart charts statement-level branches. A ternary inside a widget argument is a decision to a reader but not a statement to a parser, so UI-building code charts thin — which is why the entry picker ranks by how many branches would actually be *drawn*, not how many exist.
- Free-tier LLM providers meter tokens per minute (Groq: 8k/min on `gpt-oss-120b`). Prompts are budgeted to fit one deep investigation inside that, but asking several deep questions back to back can still return "the AI service may be at capacity" — wait a minute or use quick mode.

## License

MIT — see [LICENSE](LICENSE).
