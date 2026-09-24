// Onboarding guide, live investigation streaming, the file detail
// drawer, and impact analysis.
//
// Kept beside app.js rather than inside it: app.js owns ingest, ask
// and the diagram, and these four features are one connected workflow
// of their own (guide -> file -> ask -> investigate -> impact). They
// talk to app.js only through window.RepoPilot, which app.js defines.

(function () {
    "use strict";

    const RP = window.RepoPilot;

    // ---------------------------------------------------------------
    // Shared helpers
    // ---------------------------------------------------------------

    function el(id) {
        return document.getElementById(id);
    }

    function clear(node) {
        node.innerHTML = "";
    }

    // Every path rendered below came from a repo we cloned, so it is
    // untrusted text: it goes in via textContent, never innerHTML.
    function chip(text, { title, onClick, className } = {}) {
        const li = document.createElement("li");
        const node = document.createElement(onClick ? "button" : "span");
        node.textContent = text;
        node.className = "chip" + (className ? " " + className : "");
        if (title) node.title = title;
        if (onClick) {
            node.type = "button";
            node.addEventListener("click", onClick);
        }
        li.appendChild(node);
        return li;
    }

    async function postJson(url, body) {
        const res = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(data.detail || `Request failed (${res.status}).`);
        }
        return data;
    }

    function repoKeys() {
        const repo = RP.getCurrentRepo();
        if (!repo) return null;
        return { repo_full_name: repo.repoFullName, commit_sha: repo.commitSha };
    }

    // ===============================================================
    // Feature 1 — Onboarding guide
    // ===============================================================

    const onboardingBtn = el("onboarding-btn");
    const onboardingStatus = el("onboarding-status");
    const onboardingGuide = el("onboarding-guide");

    async function loadOnboarding() {
        const keys = repoKeys();
        if (!keys) {
            RP.setStatus(onboardingStatus, "Ingest a repo first.", "error");
            return;
        }

        RP.setStatus(onboardingStatus, "reading the dependency graph and building your reading order...", "loading");
        onboardingBtn.disabled = true;
        onboardingGuide.classList.add("hidden");

        try {
            const data = await postJson("/api/onboarding", keys);
            renderOnboarding(data);
            // Only offered once a reading order exists: the handbook is
            // built from the same guide, so before this it would be empty.
            const handbookBtn = el("handbook-btn");
            if (handbookBtn) handbookBtn.classList.remove("hidden");
            RP.setStatus(
                onboardingStatus,
                data.graph_available
                    ? ""
                    : "no internal import graph for this repo — the order below is based on entry points and structure alone",
                data.graph_available ? "success" : "warn"
            );
        } catch (err) {
            RP.setStatus(onboardingStatus, err.message, "error");
        } finally {
            onboardingBtn.disabled = false;
        }
    }

    function renderOnboarding(data) {
        el("onboarding-overview").textContent = data.overview || "No summary available for this repository.";

        const entryList = el("entry-points-list");
        clear(entryList);
        if (data.entry_points.length === 0) {
            entryList.appendChild(chip("none detected", { className: "chip-muted" }));
        } else {
            data.entry_points.forEach((path) => {
                entryList.appendChild(chip(path, { title: "Open " + path, onClick: () => openFile(path) }));
            });
        }

        const orderList = el("reading-order-list");
        clear(orderList);
        if (data.reading_order.length === 0) {
            const li = document.createElement("li");
            li.className = "dim";
            li.textContent = "No files could be ranked — the repo may have no parsed source files.";
            orderList.appendChild(li);
        }
        data.reading_order.forEach((step) => orderList.appendChild(renderReadingStep(step)));

        const questions = el("suggested-questions");
        clear(questions);
        data.suggested_questions.forEach((q) => {
            questions.appendChild(
                chip(q, {
                    title: "Ask this",
                    onClick: () => {
                        RP.clearAskContext();
                        RP.askQuestion(q);
                    },
                })
            );
        });

        onboardingGuide.classList.remove("hidden");
    }

    function renderReadingStep(step) {
        const li = document.createElement("li");
        li.className = "reading-step";

        const header = document.createElement("div");
        header.className = "reading-step-header";

        const pathBtn = document.createElement("button");
        pathBtn.type = "button";
        pathBtn.className = "reading-step-path";
        pathBtn.textContent = step.file_path;
        pathBtn.title = "Open " + step.file_path;
        pathBtn.addEventListener("click", () => openFile(step.file_path));
        header.appendChild(pathBtn);

        if (step.is_entry_point) {
            const badge = document.createElement("span");
            badge.className = "badge badge-entry";
            badge.textContent = "entry point";
            header.appendChild(badge);
        }
        li.appendChild(header);

        const why = document.createElement("p");
        why.className = "reading-step-why";
        why.textContent = step.why_it_matters;
        li.appendChild(why);

        const understand = document.createElement("p");
        understand.className = "reading-step-understand";
        understand.textContent = "Take away: " + step.what_to_understand;
        li.appendChild(understand);

        const facts = [];
        if (step.key_symbols.length) facts.push("defines " + step.key_symbols.join(", "));
        if (step.depended_on_by.length) facts.push(step.depended_on_by.length + " module(s) import it");
        if (step.depends_on.length) facts.push("imports " + step.depends_on.length + " module(s)");
        if (facts.length) {
            const meta = document.createElement("p");
            meta.className = "reading-step-meta dim";
            meta.textContent = facts.join(" · ");
            li.appendChild(meta);
        }
        return li;
    }

    async function downloadHandbook() {
        const keys = repoKeys();
        if (!keys) return;
        const btn = el("handbook-btn");
        btn.disabled = true;
        RP.setStatus(onboardingStatus, "gathering the reading order and the health findings into one document...", "loading");
        try {
            const res = await fetch("/api/handbook", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(keys),
            });
            if (!res.ok) {
                let detail = "The handbook could not be built.";
                try { detail = (await res.json()).detail || detail; } catch (e) { /* not JSON */ }
                throw new Error(detail);
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = keys.repo_full_name.replace(/[^\w-]/g, "-") + "-handbook.md";
            a.click();
            URL.revokeObjectURL(url);
            RP.setStatus(onboardingStatus, "handbook downloaded. Commit it next to the code and regenerate it when the code moves.", "success");
        } catch (err) {
            RP.setStatus(onboardingStatus, err.message, "error");
        } finally {
            btn.disabled = false;
        }
    }

    if (onboardingBtn) onboardingBtn.addEventListener("click", loadOnboarding);
    const handbookBtn = el("handbook-btn");
    if (handbookBtn) handbookBtn.addEventListener("click", downloadHandbook);

    // ===============================================================
    // Feature 2 — Live investigation streaming
    // ===============================================================

    const liveSection = el("live-investigation");
    const liveTimeline = el("live-timeline");
    const liveState = el("live-state");

    // Icons keep the trail scannable without reading every line. Types
    // mirror InvestigationEventType on the server.
    const EVENT_ICONS = {
        investigation_started: "▶",
        search_started: "⌕",
        search_result: "✓",
        graph_traversal_started: "↦",
        graph_result: "✓",
        source_inspection: "▤",
        answer_generation: "✎",
        verification: "?",
        completed: "■",
        error: "!",
    };

    // Steps that announce work about to happen stay "current" until the
    // next event arrives; the rest are done the moment they land.
    const IN_PROGRESS_TYPES = new Set([
        "investigation_started",
        "search_started",
        "graph_traversal_started",
        "source_inspection",
        "answer_generation",
    ]);

    let activeStream = null;

    function resetTimeline() {
        clear(liveTimeline);
        liveSection.classList.remove("hidden");
        liveState.textContent = "running";
        liveState.className = "live-state live-state-running";
    }

    function markPreviousDone() {
        const current = liveTimeline.querySelector(".timeline-step.current");
        if (current) {
            current.classList.remove("current");
            current.classList.add("done");
        }
    }

    function appendEvent(event) {
        markPreviousDone();

        const li = document.createElement("li");
        li.className = "timeline-step";
        if (event.type === "error") {
            li.classList.add("error");
        } else if (IN_PROGRESS_TYPES.has(event.type)) {
            li.classList.add("current");
        } else {
            li.classList.add("done");
        }

        const icon = document.createElement("span");
        icon.className = "timeline-icon";
        icon.textContent = EVENT_ICONS[event.type] || "·";
        li.appendChild(icon);

        const body = document.createElement("div");
        body.className = "timeline-body";

        const message = document.createElement("span");
        message.className = "timeline-message";
        message.textContent = event.message;
        body.appendChild(message);

        const files = (event.metadata && event.metadata.files) || [];
        if (files.length) {
            const fileList = document.createElement("ul");
            fileList.className = "timeline-files";
            files.forEach((path) => {
                fileList.appendChild(
                    chip(path, { title: "Open " + path, onClick: () => openFile(path), className: "chip-small" })
                );
            });
            body.appendChild(fileList);
        }

        li.appendChild(body);
        liveTimeline.appendChild(li);
        li.scrollIntoView({ block: "nearest" });
    }

    function finishTimeline(state, label) {
        markPreviousDone();
        liveState.textContent = label;
        liveState.className = "live-state live-state-" + state;
    }

    function closeStream() {
        if (activeStream) {
            activeStream.close();
            activeStream = null;
        }
    }

    /**
     * Runs a deep investigation over SSE. Resolves with the answer
     * payload, or rejects if the stream fails — the caller renders the
     * answer exactly as it renders a POST /api/ask response.
     */
    function streamInvestigation(question, keys) {
        return new Promise((resolve, reject) => {
            closeStream();
            resetTimeline();

            const params = new URLSearchParams({
                repo_full_name: keys.repo_full_name,
                commit_sha: keys.commit_sha,
                question: question,
                persona: RP.getPersona(),
                history: JSON.stringify(RP.conversationPayload()),
            });
            const source = new EventSource("/api/ask/stream?" + params.toString());
            activeStream = source;

            let settled = false;
            const finish = (fn, value) => {
                if (settled) return;
                settled = true;
                closeStream();
                fn(value);
            };

            Object.keys(EVENT_ICONS).forEach((type) => {
                source.addEventListener(type, (e) => {
                    let event;
                    try {
                        event = JSON.parse(e.data);
                    } catch (err) {
                        return;
                    }
                    appendEvent(event);
                    if (type === "error") {
                        finishTimeline("error", "failed");
                        finish(reject, new Error(event.message || "The investigation failed."));
                    } else if (type === "completed") {
                        finishTimeline("done", "complete");
                    }
                });
            });

            source.addEventListener("answer", (e) => {
                try {
                    finish(resolve, JSON.parse(e.data));
                } catch (err) {
                    finish(reject, new Error("The investigation returned a malformed answer."));
                }
            });

            source.onerror = () => {
                // EventSource also fires this on a normal server close,
                // which is why the answer/error handlers settle first.
                if (!settled) {
                    finishTimeline("error", "disconnected");
                    finish(reject, new Error("Lost connection to the investigation stream."));
                }
            };
        });
    }

    RP.streamInvestigation = streamInvestigation;
    RP.hideInvestigationTimeline = function () {
        closeStream();
        liveSection.classList.add("hidden");
    };

    // ===============================================================
    // Feature 3 — File detail drawer (source -> ask)
    // ===============================================================

    const filePanel = el("file-panel");
    const filePanelStatus = el("file-panel-status");
    const symbolSelect = el("symbol-select");

    let openFilePath = null;

    function setPanelOpen(open) {
        filePanel.classList.toggle("hidden", !open);
        filePanel.hidden = !open;
        document.body.classList.toggle("has-file-panel", open);
    }

    function selectPanelTab(panelId) {
        document.querySelectorAll(".panel-tab-btn").forEach((b) => {
            b.classList.toggle("active", b.dataset.panel === panelId);
        });
        document.querySelectorAll(".panel-content").forEach((c) => {
            c.classList.toggle("active", c.id === panelId);
        });
    }

    async function openFile(path, { tab } = {}) {
        const keys = repoKeys();
        if (!keys) return;

        openFilePath = path;
        setPanelOpen(true);
        selectPanelTab(tab || "panel-source");

        el("file-panel-path").textContent = path;
        el("file-panel-meta").textContent = "";
        el("file-source-code").textContent = "";
        resetImpact();
        RP.setStatus(filePanelStatus, "loading source...", "loading");

        try {
            const data = await postJson("/api/file", { ...keys, file_path: path });
            renderFile(data);
            RP.setStatus(filePanelStatus, "", "success");
        } catch (err) {
            RP.setStatus(filePanelStatus, err.message, "error");
            el("file-source-code").textContent = "";
            el("file-panel-meta").textContent = "";
        }
    }

    function renderFile(data) {
        const meta = [];
        if (data.language) meta.push(data.language);
        if (data.line_count) meta.push(data.line_count + " lines");
        if (data.truncated) meta.push("truncated");
        el("file-panel-meta").textContent = meta.join(" · ");

        el("file-source-code").textContent =
            data.content || "(this file has no stored content — it may have been skipped at ingest)";

        const symbols = el("file-symbols");
        clear(symbols);
        if (!data.symbols.length) {
            symbols.appendChild(chip("no parsed symbols", { className: "chip-muted" }));
        }
        data.symbols.forEach((s) => {
            symbols.appendChild(
                chip(s.name, {
                    title: `${s.kind}, lines ${s.start_line}-${s.end_line}`,
                    onClick: () => runImpact(s.name),
                })
            );
        });

        renderPathChips(el("file-imports"), data.imports, "imports nothing in this repo");
        renderPathChips(el("file-imported-by"), data.imported_by, "nothing in this repo imports it");

        // Repopulate the symbol picker for impact analysis.
        clear(symbolSelect);
        const whole = document.createElement("option");
        whole.value = "";
        whole.textContent = "whole file";
        symbolSelect.appendChild(whole);
        data.symbols.forEach((s) => {
            const option = document.createElement("option");
            option.value = s.name;
            option.textContent = s.name + "()";
            symbolSelect.appendChild(option);
        });
        symbolSelect.disabled = data.symbols.length === 0;
    }

    function renderPathChips(list, paths, emptyLabel) {
        clear(list);
        if (!paths.length) {
            list.appendChild(chip(emptyLabel, { className: "chip-muted" }));
            return;
        }
        paths.forEach((path) => {
            list.appendChild(chip(path, { title: "Open " + path, onClick: () => openFile(path) }));
        });
    }

    el("file-panel-close").addEventListener("click", () => setPanelOpen(false));
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !filePanel.hidden) setPanelOpen(false);
    });
    document.querySelectorAll(".panel-tab-btn").forEach((btn) => {
        btn.addEventListener("click", () => selectPanelTab(btn.dataset.panel));
    });

    el("ask-about-file-btn").addEventListener("click", () => {
        if (!openFilePath) return;
        RP.setAskContext(openFilePath);
        setPanelOpen(false);
        RP.askQuestion(`What does ${openFilePath} do?`);
    });

    RP.openFile = openFile;

    // ===============================================================
    // Feature 4 — Impact analysis
    // ===============================================================

    const impactResult = el("impact-result");
    const impactEmpty = el("impact-empty");

    function resetImpact() {
        impactResult.classList.add("hidden");
        impactEmpty.classList.remove("hidden");
        RP.setImpactHighlight(null);
    }

    async function runImpact(symbol) {
        const keys = repoKeys();
        if (!keys || !openFilePath) return;

        selectPanelTab("panel-impact");
        const chosen = symbol !== undefined ? symbol : symbolSelect.value;
        if (symbol !== undefined) symbolSelect.value = symbol || "";

        RP.setStatus(
            filePanelStatus,
            `tracing what depends on ${chosen ? chosen + "()" : openFilePath}...`,
            "loading"
        );
        impactEmpty.classList.add("hidden");
        impactResult.classList.add("hidden");

        try {
            const data = await postJson("/api/impact", {
                ...keys,
                file_path: openFilePath,
                symbol: chosen || null,
            });
            renderImpact(data);
            RP.setStatus(filePanelStatus, "", "success");
        } catch (err) {
            RP.setStatus(filePanelStatus, err.message, "error");
            impactEmpty.classList.remove("hidden");
        }
    }

    function renderImpact(data) {
        el("impact-summary").textContent = data.summary;

        const stats = el("impact-stats");
        clear(stats);
        const counts = [
            ["direct", data.direct.length],
            ["indirect", data.indirect.length],
            ["max depth", data.max_depth],
        ];
        if (data.callers.length) counts.push(["callers", data.callers.length]);
        if (data.entry_points_reached.length) {
            counts.push(["entry points", data.entry_points_reached.length]);
        }
        counts.forEach(([label, value]) => {
            const box = document.createElement("div");
            box.className = "impact-stat";
            const n = document.createElement("strong");
            n.textContent = String(value);
            const caption = document.createElement("span");
            caption.textContent = label;
            box.appendChild(n);
            box.appendChild(caption);
            stats.appendChild(box);
        });

        renderImpactList(el("impact-direct"), data.direct, "nothing imports this directly");
        renderImpactList(el("impact-indirect"), data.indirect, "no indirect dependents");
        renderImpactList(
            el("impact-callers"),
            data.callers,
            data.target_symbol
                ? "no code calls this symbol by name"
                : "pick a symbol above to trace callers"
        );

        impactResult.classList.remove("hidden");
        impactEmpty.classList.add("hidden");

        // Light up the blast radius on the diagram, if one is drawn.
        RP.setImpactHighlight({
            target: data.target_path,
            direct: data.direct.map((n) => n.path),
            indirect: data.indirect.map((n) => n.path),
        });
    }

    function renderImpactList(list, nodes, emptyLabel) {
        clear(list);
        if (!nodes.length) {
            const li = document.createElement("li");
            li.className = "dim impact-empty-row";
            li.textContent = emptyLabel;
            list.appendChild(li);
            return;
        }
        nodes.forEach((node) => {
            const li = document.createElement("li");
            li.className = "impact-row " + (node.is_direct ? "impact-direct" : "impact-indirect");

            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "impact-path";
            btn.textContent = node.path;
            btn.title = "Open " + node.path;
            btn.addEventListener("click", () => openFile(node.path));
            li.appendChild(btn);

            const depth = document.createElement("span");
            depth.className = "badge " + (node.is_direct ? "badge-direct" : "badge-indirect");
            depth.textContent = node.is_direct ? "direct" : "depth " + node.depth;
            li.appendChild(depth);

            const details = [];
            if (node.via && node.via.length) details.push("via " + node.via.join(", "));
            if (node.symbols && node.symbols.length) {
                details.push(node.symbols.slice(0, 4).join(", "));
            }
            if (details.length) {
                const meta = document.createElement("span");
                meta.className = "impact-meta dim";
                meta.textContent = details.join(" · ");
                li.appendChild(meta);
            }
            list.appendChild(li);
        });
    }

    el("impact-btn").addEventListener("click", () => runImpact());
    symbolSelect.addEventListener("change", () => {
        if (!impactResult.classList.contains("hidden")) runImpact();
    });

    // ===============================================================
    // Feature 5 — Code health report card
    // ===============================================================

    const healthBtn = el("health-btn");
    const healthStatus = el("health-status");
    const healthReport = el("health-report");

    const CHECK_LABELS = {
        circular_import: "circular imports",
        hub_module: "hub modules",
        untested_module: "untested modules",
        long_function: "long functions",
        large_file: "large files",
        unreferenced_symbol: "unreferenced symbols",
    };
    const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 };

    // "low" on its own tells a reader nothing. Each severity gets a
    // verb (what to do) and a sentence (why it is in that band).
    const SEVERITY_META = {
        high: { label: "worth fixing", blurb: "These slow down or mislead anyone working in the code. Start here." },
        medium: { label: "worth a look", blurb: "Not urgent, but they make the code harder to change than it needs to be." },
        low: { label: "minor", blurb: "Small cleanups. Nothing here is broken — fix them when you are already in the file." },
    };

    const GRADE_MEANING = {
        A: "Healthy. Nothing here needs attention right now.",
        B: "In good shape. A few things worth tidying when you are nearby.",
        C: "Workable, with some real friction worth planning in.",
        D: "Rough edges that will slow down anyone new to this code.",
        F: "Hard to work in safely. Worth addressing before adding features.",
    };

    const CHECK_EXPLAINERS = {
        circular_import: "modules that import each other in a loop",
        hub_module: "modules that many others depend on",
        untested_module: "code no test file imports",
        long_function: "functions long enough to hide several jobs",
        large_file: "files large enough to be hard to navigate",
        unreferenced_symbol: "functions nothing else appears to call",
    };

    let healthFindings = [];
    let healthFilter = "all";

    async function loadHealth() {
        const keys = repoKeys();
        if (!keys) {
            RP.setStatus(healthStatus, "Ingest a repo first.", "error");
            return;
        }
        RP.setStatus(healthStatus, "analysing the parsed index...", "loading");
        healthBtn.disabled = true;
        healthReport.classList.add("hidden");
        try {
            const data = await postJson("/api/code-health", keys);
            renderHealth(data);
            RP.setStatus(healthStatus, "", "success");
        } catch (err) {
            RP.setStatus(healthStatus, err.message, "error");
        } finally {
            healthBtn.disabled = false;
        }
    }

    function renderHealth(data) {
        const grade = el("health-grade");
        grade.textContent = data.grade;
        grade.className = "health-grade grade-" + data.grade.toLowerCase();
        el("health-score-value").textContent = data.score;

        const counts = { high: 0, medium: 0, low: 0 };
        data.findings.forEach((f) => { counts[f.severity] += 1; });
        el("health-verdict").textContent = GRADE_MEANING[data.grade] || "";
        el("health-next").textContent = nextStepSentence(counts, data.findings);

        const stats = el("health-stats");
        clear(stats);
        [
            data.source_file_count + " source files",
            data.test_file_count + " test files",
            data.symbol_count + " symbols",
            data.findings.length + " findings",
        ].forEach((text) => {
            const li = document.createElement("li");
            li.textContent = text;
            stats.appendChild(li);
        });

        const deductions = el("health-deductions");
        clear(deductions);
        const entries = Object.entries(data.deductions).sort((a, b) => b[1] - a[1]);
        if (entries.length === 0) {
            const li = document.createElement("li");
            li.className = "dim";
            li.textContent = "none — no findings";
            deductions.appendChild(li);
        }
        entries.forEach(([check, points]) => {
            const li = document.createElement("li");
            const label = document.createElement("span");
            label.textContent = CHECK_LABELS[check] || check;
            const bar = document.createElement("span");
            bar.className = "deduction-bar";
            bar.style.width = Math.max(4, points * 6) + "px";
            const value = document.createElement("span");
            value.className = "dim";
            value.textContent = "-" + points;
            li.append(label, bar, value);
            deductions.appendChild(li);
        });

        healthFindings = data.findings.slice().sort(
            (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || (b.metric || 0) - (a.metric || 0)
        );
        healthFilter = "all";
        renderHealthFilters();
        renderHealthFindings();
        healthReport.classList.remove("hidden");
        el("health-download-btn").classList.remove("hidden");
    }

    function nextStepSentence(counts, findings) {
        if (findings.length === 0) return "No findings — nothing to act on.";
        const band = counts.high ? "high" : counts.medium ? "medium" : "low";
        const first = findings.find((f) => f.severity === band);
        const n = counts[band];
        const noun = n === 1 ? "finding" : "findings";
        return `${n} ${SEVERITY_META[band].label} ${noun}. Start with: ${first.title}`;
    }

    function renderHealthFilters() {
        const filters = el("health-filters");
        clear(filters);
        const present = new Set(healthFindings.map((f) => f.check));
        const options = [["all", "all (" + healthFindings.length + ")"]].concat(
            Object.keys(CHECK_LABELS)
                .filter((c) => present.has(c))
                .map((c) => [c, CHECK_LABELS[c] + " (" + healthFindings.filter((f) => f.check === c).length + ")"])
        );
        options.forEach(([value, label]) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "chip chip-small" + (value === healthFilter ? " chip-active" : "");
            btn.textContent = label;
            if (CHECK_EXPLAINERS[value]) btn.title = CHECK_EXPLAINERS[value];
            btn.addEventListener("click", () => {
                healthFilter = value;
                renderHealthFilters();
                renderHealthFindings();
            });
            filters.appendChild(btn);
        });
    }

    function renderHealthFindings() {
        const list = el("health-findings");
        clear(list);
        const shown = healthFindings.filter((f) => healthFilter === "all" || f.check === healthFilter);
        if (shown.length === 0) {
            const li = document.createElement("li");
            li.className = "dim";
            li.textContent = "no findings — nice.";
            list.appendChild(li);
            return;
        }

        // A heading before each severity run, so a reader meets the
        // explanation of the band before the findings inside it.
        let currentBand = null;
        shown.forEach((f) => {
            if (f.severity !== currentBand) {
                currentBand = f.severity;
                const head = document.createElement("li");
                head.className = "severity-heading severity-" + f.severity;
                const name = document.createElement("span");
                name.className = "severity-name";
                name.textContent = SEVERITY_META[f.severity].label;
                const blurb = document.createElement("span");
                blurb.className = "severity-blurb";
                blurb.textContent = SEVERITY_META[f.severity].blurb;
                head.append(name, blurb);
                list.appendChild(head);
            }
            const li = document.createElement("li");
            li.className = "health-finding";

            const header = document.createElement("div");
            header.className = "health-finding-header";
            const badge = document.createElement("span");
            badge.className = "badge badge-" + f.severity;
            badge.textContent = CHECK_LABELS[f.check] || f.check;
            badge.title = CHECK_EXPLAINERS[f.check] || "";
            const title = document.createElement("span");
            title.className = "health-finding-title";
            title.textContent = f.title;
            header.append(badge, title);
            li.appendChild(header);

            const detail = document.createElement("p");
            detail.className = "health-finding-detail dim";
            detail.textContent = f.detail;
            li.appendChild(detail);

            if (f.files.length) {
                const files = document.createElement("ul");
                files.className = "chip-list";
                f.files.forEach((path) => {
                    const label = f.line && f.files.length === 1 ? path + ":" + f.line : path;
                    files.appendChild(chip(label, { title: "Open " + path, onClick: () => openFile(path) }));
                });
                li.appendChild(files);
            }
            list.appendChild(li);
        });
    }

    async function downloadHealthReport() {
        const keys = repoKeys();
        if (!keys) return;
        const btn = el("health-download-btn");
        btn.disabled = true;
        try {
            const res = await fetch("/api/code-health/report", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(keys),
            });
            if (!res.ok) throw new Error("The report could not be generated.");
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = keys.repo_full_name.replace(/[^\w-]/g, "-") + "-code-health.md";
            a.click();
            URL.revokeObjectURL(url);
        } catch (err) {
            RP.setStatus(healthStatus, err.message, "error");
        } finally {
            btn.disabled = false;
        }
    }

    if (healthBtn) healthBtn.addEventListener("click", loadHealth);
    const healthDownloadBtn = el("health-download-btn");
    if (healthDownloadBtn) healthDownloadBtn.addEventListener("click", downloadHealthReport);
})();
