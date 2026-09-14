// Simple vanilla JS — no build step, no framework. Talks to our
// FastAPI JSON endpoints and renders results into the page.

let currentRepo = null; // { repoFullName, commitSha }

const ingestForm = document.getElementById("ingest-form");
const ingestStatus = document.getElementById("ingest-status");
const repoInfo = document.getElementById("repo-info");
const tabsSection = document.getElementById("tabs");

// --- Recent repos (localStorage) ---
const RECENT_REPOS_KEY = "repoPilot.recentRepos";
const MAX_RECENT_REPOS = 8;

const recentReposSection = document.getElementById("recent-repos-section");
const recentReposList = document.getElementById("recent-repos-list");

function getRecentRepos() {
    try {
        const raw = localStorage.getItem(RECENT_REPOS_KEY);
        return raw ? JSON.parse(raw) : [];
    } catch (err) {
        console.warn("Failed to read recent repos from localStorage:", err);
        return [];
    }
}

function saveRecentRepo(entry) {
    try {
        let list = getRecentRepos();
        // Remove any existing entry for the same repo before re-adding,
        // so re-ingesting an already-recent repo just moves it to the
        // top instead of creating a duplicate.
        list = list.filter((r) => r.repoUrl !== entry.repoUrl);
        list.unshift(entry);
        list = list.slice(0, MAX_RECENT_REPOS);
        localStorage.setItem(RECENT_REPOS_KEY, JSON.stringify(list));
        renderRecentRepos();
    } catch (err) {
        console.warn("Failed to save recent repo to localStorage:", err);
    }
}

function removeRecentRepo(repoUrl) {
    const list = getRecentRepos().filter((r) => r.repoUrl !== repoUrl);
    localStorage.setItem(RECENT_REPOS_KEY, JSON.stringify(list));
    renderRecentRepos();
}

function renderRecentRepos() {
    const list = getRecentRepos();

    if (list.length === 0) {
        recentReposSection.classList.add("hidden");
        return;
    }

    recentReposSection.classList.remove("hidden");
    recentReposList.innerHTML = "";

    list.forEach((entry) => {
        const li = document.createElement("li");
        li.className = "recent-repo-item";

        const nameSpan = document.createElement("span");
        nameSpan.className = "recent-repo-name";
        nameSpan.textContent = entry.repoFullName;
        nameSpan.title = entry.repoUrl;

        const removeBtn = document.createElement("button");
        removeBtn.className = "recent-repo-remove";
        removeBtn.textContent = "×";
        removeBtn.title = "Remove from recent";
        removeBtn.addEventListener("click", (e) => {
            e.stopPropagation(); // don't trigger the li's click (re-ingest) when removing
            removeRecentRepo(entry.repoUrl);
        });

        li.addEventListener("click", () => {
            document.getElementById("repo-url").value = entry.repoUrl;
            ingestForm.requestSubmit(); // re-ingest; hits cache instantly if nothing changed
        });

        li.appendChild(nameSpan);
        li.appendChild(removeBtn);
        recentReposList.appendChild(li);
    });
}

// Render on page load, in case there's already saved history.
renderRecentRepos();

ingestForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const repoUrl = document.getElementById("repo-url").value.trim();
    const btn = document.getElementById("ingest-btn");

    setStatus(ingestStatus, "Ingesting repo... this can take a minute or two.", "loading");
    btn.disabled = true;

    try {
        const res = await fetch("/api/ingest", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_url: repoUrl }),
        });

        const data = await res.json();

        if (!res.ok) {
            setStatus(ingestStatus, data.detail || "Ingestion failed.", "error");
            return;
        }

        currentRepo = { repoFullName: data.repo_full_name, commitSha: data.commit_sha };

        setStatus(
            ingestStatus,
            `ingested ${data.repo_full_name} (${data.was_cached ? "from cache" : "fresh"})`,
            "success"
        );

        document.getElementById("repo-summary").textContent = data.summary;
        document.getElementById("repo-files").textContent = `${data.file_count} files`;
        document.getElementById("repo-stack").textContent = data.detected_stack.join(", ") || "stack not detected";

        repoInfo.classList.remove("hidden");
        tabsSection.classList.remove("hidden");

        saveRecentRepo({
            repoUrl: repoUrl,
            repoFullName: data.repo_full_name,
            commitSha: data.commit_sha,
            summary: data.summary,
            timestamp: Date.now(),
        });
    } catch (err) {
        setStatus(ingestStatus, "Network error: " + err.message, "error");
    } finally {
        btn.disabled = false;
    }
});

// --- Tabs ---
document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(btn.dataset.tab).classList.add("active");
    });
});

// --- Ask ---
const askForm = document.getElementById("ask-form");
const askStatus = document.getElementById("ask-status");
const answerBox = document.getElementById("answer-box");

askForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!currentRepo) {
        setStatus(askStatus, "Ingest a repo first.", "error");
        return;
    }

    const question = document.getElementById("question-input").value.trim();
    const deepMode = document.getElementById("deep-mode-checkbox").checked;
    const btn = document.getElementById("ask-btn");

    setStatus(askStatus, "Thinking...", "loading");
    answerBox.classList.add("hidden");
    btn.disabled = true;

    try {
        const res = await fetch("/api/ask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                repo_full_name: currentRepo.repoFullName,
                commit_sha: currentRepo.commitSha,
                question: question,
                deep_mode: deepMode,
            }),
        });

        const data = await res.json();

        if (!res.ok) {
            setStatus(askStatus, data.detail || "Failed to answer.", "error");
            return;
        }

        setStatus(askStatus, "", "success");

        // Render the LLM's Markdown output as real HTML, then sanitize
        // it before insertion. This is NOT optional: the answer text
        // ultimately traces back to repo content the LLM was shown, so
        // we never trust it enough to skip DOMPurify, even though it's
        // "our own" model's output.
        const rawHtml = marked.parse(data.answer_text);
        const safeHtml = DOMPurify.sanitize(rawHtml);
        document.getElementById("answer-text").innerHTML = safeHtml;

        document.getElementById("answer-meta").textContent =
            `Mode: ${data.mode_used} · Confidence: ${(data.confidence * 100).toFixed(0)}% · Steps: ${data.steps_taken}`;

        const list = document.getElementById("citations-list");
        list.innerHTML = "";
        data.citations.forEach((c) => {
            const li = document.createElement("li");
            li.textContent = c;
            list.appendChild(li);
        });

        answerBox.classList.remove("hidden");
    } catch (err) {
        setStatus(askStatus, "Network error: " + err.message, "error");
    } finally {
        btn.disabled = false;
    }
});

// --- Diagram ---
const diagramBtn = document.getElementById("diagram-btn");
const diagramStatus = document.getElementById("diagram-status");
const mermaidContainer = document.getElementById("mermaid-container");
const folderToggleCheckbox = document.getElementById("folder-toggle-checkbox");
const resetViewBtn = document.getElementById("reset-view-btn");
const downloadPngBtn = document.getElementById("download-png-btn");
const downloadSvgBtn = document.getElementById("download-svg-btn");

let panZoomInstance = null; // holds the active svg-pan-zoom controller

mermaid.initialize({
    startOnLoad: false,
    theme: "base",
    themeVariables: {
        background: "#121613",
        mainBkg: "#121613",
        primaryColor: "#121613",
        primaryTextColor: "#F2F6F3",
        primaryBorderColor: "#6EE7A8",
        secondaryColor: "#0F1310",
        tertiaryColor: "#0F1310",
        lineColor: "#6EE7A8",
        textColor: "#C7D3CC",
        nodeBorder: "#6EE7A8",
        clusterBkg: "#0F1310",
        clusterBorder: "#23302A",
        edgeLabelBackground: "#121613",
        fontFamily: "JetBrains Mono, monospace",
    },
    flowchart: { useMaxWidth: false },
});

async function generateDiagram() {
    if (!currentRepo) {
        setStatus(diagramStatus, "Ingest a repo first.", "error");
        return;
    }

    setStatus(diagramStatus, "Generating diagram...", "loading");
    diagramBtn.disabled = true;

    // Destroy any previous pan/zoom instance before replacing the SVG —
    // leaving a stale instance attached to a removed node leaks memory
    // and its event listeners keep firing on nothing.
    if (panZoomInstance) {
        panZoomInstance.destroy();
        panZoomInstance = null;
    }

    try {
        const orientationCheckbox = document.getElementById("orientation-checkbox");
        const res = await fetch("/api/diagram", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                repo_full_name: currentRepo.repoFullName,
                commit_sha: currentRepo.commitSha,
                orientation: orientationCheckbox && orientationCheckbox.checked ? "LR" : "TD",
                group_by_folder: folderToggleCheckbox.checked,
            }),
        });

        const data = await res.json();

        if (!res.ok) {
            setStatus(diagramStatus, data.detail || "Failed to generate diagram.", "error");
            return;
        }

        if (data.granularity === "folder") {
            setStatus(
                diagramStatus,
                `grouped to folder level for readability (${data.total_files} files with internal imports) · edge labels = number of imports`,
                "success"
            );
        } else {
            setStatus(diagramStatus, "", "success");
        }

        const diagramEl = document.getElementById("mermaid-diagram");
        diagramEl.removeAttribute("data-processed");
        diagramEl.textContent = data.mermaid_syntax;
        mermaidContainer.classList.remove("hidden");
        mermaidContainer.classList.add("pan-zoom-active");

        await mermaid.run({ nodes: [diagramEl] });

        const svgEl = diagramEl.querySelector("svg");
        if (svgEl) {
            // controlIconsEnabled is OFF deliberately: svg-pan-zoom's
            // built-in +/- buttons get injected as real elements inside
            // this same SVG, which taints canvas-based PNG export later.
            // Scroll-wheel zoom, drag-to-pan, and our own Reset View
            // button already cover the same functionality without that
            // side effect.
            panZoomInstance = svgPanZoom(svgEl, {
                zoomEnabled: true,
                panEnabled: true,
                controlIconsEnabled: false,
                fit: true,
                center: true,
                minZoom: 0.2,
                maxZoom: 10,
            });
        }

        resetViewBtn.classList.remove("hidden");
        downloadPngBtn.classList.remove("hidden");
        downloadSvgBtn.classList.remove("hidden");
    } catch (err) {
        setStatus(diagramStatus, "Network error: " + err.message, "error");
    } finally {
        diagramBtn.disabled = false;
    }
}

diagramBtn.addEventListener("click", generateDiagram);

// Re-generate immediately when the folder-grouping or orientation
// toggles change, so it feels like a live view option rather than a
// "click twice" step.
folderToggleCheckbox.addEventListener("change", () => {
    if (currentRepo && !mermaidContainer.classList.contains("hidden")) {
        generateDiagram();
    }
});
document.getElementById("orientation-checkbox").addEventListener("change", () => {
    if (currentRepo && !mermaidContainer.classList.contains("hidden")) {
        generateDiagram();
    }
});

resetViewBtn.addEventListener("click", () => {
    if (panZoomInstance) {
        panZoomInstance.resetZoom();
        panZoomInstance.center();
    }
});

downloadSvgBtn.addEventListener("click", () => {
    const svgEl = document.querySelector("#mermaid-diagram svg");
    if (!svgEl) return;

    const serializer = new XMLSerializer();
    const svgString = serializer.serializeToString(svgEl);
    const blob = new Blob([svgString], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = `${(currentRepo?.repoFullName || "diagram").replace("/", "_")}.svg`;
    a.click();
    URL.revokeObjectURL(url);
});

downloadPngBtn.addEventListener("click", () => {
    const svgEl = document.querySelector("#mermaid-diagram svg");
    if (!svgEl) return;

    const clone = svgEl.cloneNode(true);

    // Force every text element to a safe, non-external font before
    // rasterizing. Mermaid's SVG inherits "Space Grotesk"/"Inter" from
    // the page's Google Fonts <link>, and canvas rasterization taints
    // itself trying to resolve that external font reference. Overriding
    // to a generic system font sidesteps the taint entirely.
    const styleEl = document.createElementNS("http://www.w3.org/2000/svg", "style");
    styleEl.textContent = "* { font-family: Arial, sans-serif !important; }";
    clone.insertBefore(styleEl, clone.firstChild);

    // Strip anything else that could reference an external resource —
    // these also taint the canvas even without an explicit external
    // <link> tag inside the SVG itself.
    clone.querySelectorAll("foreignObject, image, link").forEach((el) => el.remove());

    const serializer = new XMLSerializer();
    const svgString = serializer.serializeToString(clone);
    const svgBlob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(svgBlob);

    const img = new Image();

    img.onerror = (err) => {
        console.error("PNG export failed: image failed to load from SVG blob", err);
        setStatus(diagramStatus, "PNG export failed — try Download SVG instead.", "error");
        URL.revokeObjectURL(url);
    };

    img.onload = () => {
        try {
            const canvas = document.createElement("canvas");
            const width = svgEl.viewBox.baseVal.width || svgEl.clientWidth || 1200;
            const height = svgEl.viewBox.baseVal.height || svgEl.clientHeight || 800;
            const scale = 2; // sharper export than on-screen resolution
            canvas.width = width * scale;
            canvas.height = height * scale;

            const ctx = canvas.getContext("2d");
            ctx.scale(scale, scale);
            ctx.fillStyle = "#121613"; // matches --surface, so the exported PNG isn't a jarring white square against the dark diagram panel
            ctx.fillRect(0, 0, width, height);
            ctx.drawImage(img, 0, 0, width, height);
            URL.revokeObjectURL(url);

            canvas.toBlob((blob) => {
                if (!blob) {
                    console.error("PNG export failed: canvas.toBlob returned null (likely a tainted canvas)");
                    setStatus(diagramStatus, "PNG export failed — try Download SVG instead.", "error");
                    return;
                }
                const pngUrl = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = pngUrl;
                a.download = `${(currentRepo?.repoFullName || "diagram").replace("/", "_")}.png`;
                a.click();
                URL.revokeObjectURL(pngUrl);
            });
        } catch (err) {
            console.error("PNG export failed:", err);
            setStatus(diagramStatus, "PNG export failed — try Download SVG instead.", "error");
        }
    };

    img.src = url;
});

// --- Helper ---
function setStatus(el, message, type) {
    el.textContent = message;
    el.className = message ? `status-${type}` : "";
}