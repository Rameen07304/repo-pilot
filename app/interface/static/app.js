// Simple vanilla JS — no build step, no framework. Talks to our
// FastAPI JSON endpoints and renders results into the page.

let currentRepo = null; // { repoFullName, commitSha }

const ingestForm = document.getElementById("ingest-form");
const ingestStatus = document.getElementById("ingest-status");
const repoInfo = document.getElementById("repo-info");
const tabsSection = document.getElementById("tabs");

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
            `✅ Ingested ${data.repo_full_name} (${data.was_cached ? "from cache" : "fresh"})`,
            "success"
        );

        document.getElementById("repo-summary").textContent = data.summary;
        document.getElementById("repo-files").textContent = `${data.file_count} files`;
        document.getElementById("repo-stack").textContent = data.detected_stack.join(", ") || "stack not detected";

        repoInfo.classList.remove("hidden");
        tabsSection.classList.remove("hidden");
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

mermaid.initialize({ startOnLoad: false, theme: "default" });

diagramBtn.addEventListener("click", async () => {
    if (!currentRepo) {
        setStatus(diagramStatus, "Ingest a repo first.", "error");
        return;
    }

    setStatus(diagramStatus, "Generating diagram...", "loading");
    diagramBtn.disabled = true;

    try {
        const res = await fetch("/api/diagram", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                repo_full_name: currentRepo.repoFullName,
                commit_sha: currentRepo.commitSha,
            }),
        });

        const data = await res.json();

        if (!res.ok) {
            setStatus(diagramStatus, data.detail || "Failed to generate diagram.", "error");
            return;
        }

        setStatus(diagramStatus, "", "success");

        const diagramEl = document.getElementById("mermaid-diagram");
        diagramEl.removeAttribute("data-processed");
        diagramEl.textContent = data.mermaid_syntax;
        mermaidContainer.classList.remove("hidden");

        await mermaid.run({ nodes: [diagramEl] });
    } catch (err) {
        setStatus(diagramStatus, "Network error: " + err.message, "error");
    } finally {
        diagramBtn.disabled = false;
    }
});

// --- Helper ---
function setStatus(el, message, type) {
    el.textContent = message;
    el.className = message ? `status-${type}` : "";
}