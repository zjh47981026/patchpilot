"use strict";
const $ = (id) => document.getElementById(id);
let token = "",
  current = null,
  imported = null,
  revision = 0,
  filter = "all",
  busy = false;
function element(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
  $("notice").hidden = !message;
}
async function api(path, data) {
  const response = await fetch(
    path,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-PatchPilot-Token": token,
          },
          body: JSON.stringify(data),
        },
  );
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed.");
  return result;
}
function setBusy(value, label) {
  busy = value;
  ["run", "import", "demo", "benchmark"].forEach(
    (id) => ($(id).disabled = value),
  );
  $("run").textContent = value
    ? label || "Reviewing change…"
    : "Review change →";
}
function show(view) {
  document
    .querySelectorAll(".view")
    .forEach((node) => (node.hidden = node.id !== "view-" + view));
  document.querySelectorAll(".nav").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === view);
    node.setAttribute(
      "aria-current",
      node.dataset.view === view ? "page" : "false",
    );
  });
  $("breadcrumb").textContent = {
    review: "Workspace / New review",
    evaluate: "Workspace / Evaluation lab",
    history: "Workspace / Review history",
  }[view];
  notice("");
  if (view === "history") loadHistory();
  window.scrollTo({ top: 0, behavior: "instant" });
}
document
  .querySelectorAll(".nav")
  .forEach((button) =>
    button.addEventListener("click", () => show(button.dataset.view)),
  );
function edited(clearSnapshot = true) {
  revision++;
  if (clearSnapshot) {
    imported = null;
    $("origin-label").hidden = true;
  }
  updateSize();
  if (current) {
    $("results-empty").hidden = false;
    $("results").hidden = true;
    $("export").hidden = true;
    current = null;
    notice("Input changed. Run a new review to inspect this version.");
  }
}
function updateSize() {
  $("diff-size").textContent =
    new TextEncoder().encode($("diff").value).length.toLocaleString() +
    " bytes";
}
["diff", "source", "title", "ai"].forEach((id) =>
  $(id).addEventListener("input", () =>
    edited(id === "diff" || id === "source"),
  ),
);
function fillInput(input) {
  revision++;
  current = null;
  imported = input.snapshot_id ? input : null;
  $("title").value = input.title;
  $("diff").value = input.diff;
  $("source").value =
    input.files && Object.keys(input.files).length
      ? JSON.stringify(input.files, null, 2)
      : "";
  $("origin-label").hidden = !input.origin;
  if (input.origin)
    $("origin-label").textContent =
      "Snapshot " +
      input.origin.head_sha.slice(0, 12) +
      " · " +
      input.origin.coverage;
  updateSize();
  $("results-empty").hidden = false;
  $("results").hidden = true;
  $("export").hidden = true;
}
$("demo").addEventListener("click", async () => {
  try {
    setBusy(true, "Loading sample…");
    fillInput(await api("/api/demo"));
    notice(
      "Sample loaded: cart state, discount calculation and receipt generation. Select Review change to inspect it.",
    );
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(false);
  }
});
$("import").addEventListener("click", async () => {
  const requestRevision = revision;
  try {
    setBusy(true, "Importing public PR…");
    notice("Fetching Python patches and source at the PR head commit…");
    const input = await api("/api/import", { url: $("pr-url").value.trim() });
    if (requestRevision !== revision) {
      notice(
        "Input changed during import. Import again to keep the current input safe.",
      );
      return;
    }
    fillInput(input);
    notice(
      input.warnings.length
        ? "Imported with coverage limits: " + input.warnings.join(" ")
        : "Imported a consistent snapshot. Ready to review.",
    );
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(false);
  }
});
$("review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const requestRevision = revision;
  try {
    let files;
    if ($("source").value.trim()) {
      try {
        files = JSON.parse($("source").value);
      } catch {
        throw new Error(
          "Full source context must be a JSON object keyed by file path.",
        );
      }
      if (!files || Array.isArray(files) || typeof files !== "object")
        throw new Error("Full source context must be a JSON object.");
    }
    setBusy(
      true,
      $("ai").checked
        ? "Running static checks and local AI…"
        : "Running static checks…",
    );
    notice(
      "Reviewing the supplied context. Imported code will not be executed.",
    );
    const payload = {
      title: $("title").value,
      diff: $("diff").value,
      files,
      use_ai: $("ai").checked,
    };
    if (imported) payload.snapshot_id = imported.snapshot_id;
    const result = await api("/api/review", payload);
    if (revision !== requestRevision) {
      notice(
        "Input changed during review. The previous result is saved in history; review the current version again.",
      );
      return;
    }
    renderResult(result);
    notice(
      result.mode === "static_fallback"
        ? "Local AI was unavailable or invalid. These are static findings; inspect the warning below."
        : "Review complete. Check the evidence and mark findings useful or incorrect.",
    );
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(false);
  }
});
function renderResult(result) {
  current = result;
  filter = "all";
  $("results-empty").hidden = true;
  $("results").hidden = false;
  $("finding-count").textContent =
    result.findings.length +
    " finding" +
    (result.findings.length === 1 ? "" : "s");
  $("result-mode").textContent =
    {
      static: "STATIC CHECKS",
      ai: "STATIC + LOCAL AI",
      static_fallback: "STATIC · AI FALLBACK",
    }[result.mode] || result.mode;
  $("latency").textContent = result.duration_ms + " ms";
  $("result-meta").textContent =
    (result.title || "Review") +
    (result.origin
      ? " · commit " + result.origin.head_sha.slice(0, 12)
      : " · supplied diff") +
    ". Line references checked; correctness still needs your judgment.";
  $("warnings").replaceChildren(
    ...result.warnings.map((text) => element("p", text, "warning")),
  );
  $("export").hidden = false;
  $("export").href =
    "/api/reviews/" + encodeURIComponent(result.id) + "/export";
  $("export").setAttribute("download", "patchpilot-review.md");
  document
    .querySelectorAll(".filter")
    .forEach((node) =>
      node.classList.toggle("active", node.dataset.filter === "all"),
    );
  renderFindings();
  renderDiff(result.files || []);
}
function renderFindings() {
  const list = $("finding-list");
  list.replaceChildren();
  const findings = current.findings.filter(
    (item) => filter === "all" || item.severity === filter,
  );
  if (!findings.length) {
    list.append(
      element(
        "p",
        current.findings.length
          ? "No findings at this severity."
          : "No findings within the checks and context available. This does not mean the change is bug-free.",
        "no-findings",
      ),
    );
    return;
  }
  for (const finding of findings) {
    const card = element("article", undefined, "finding");
    const heading = element("div", undefined, "finding-heading");
    heading.append(
      element("span", finding.severity, "severity " + finding.severity),
      element(
        "span",
        finding.source === "ai"
          ? "LOCAL AI · REVIEW SUGGESTION"
          : "STATIC · " + finding.rule,
        "finding-source",
      ),
    );
    card.append(heading, element("h3", finding.title));
    const location = element(
      "button",
      finding.path + ":" + finding.line,
      "location",
    );
    location.type = "button";
    location.addEventListener("click", () =>
      highlight(finding.path, finding.line),
    );
    card.append(
      location,
      element(
        "pre",
        finding.excerpt || "Excerpt unavailable for this saved result.",
        "evidence",
      ),
      element("p", finding.explanation),
    );
    const details = element("details");
    details.append(
      element("summary", "Suggested change & test idea"),
      element("p", finding.suggestion),
      element("p", "Test idea: " + finding.test, "test-idea"),
    );
    card.append(details);
    const feedback = element("div", undefined, "feedback");
    feedback.append(element("span", "Was this useful?"));
    for (const [vote, text] of [
      ["useful", "✓ Useful"],
      ["incorrect", "× Incorrect"],
    ]) {
      const button = element("button", text, "vote");
      button.type = "button";
      button.setAttribute(
        "aria-pressed",
        current.feedback?.[finding.id] === vote ? "true" : "false",
      );
      button.addEventListener("click", async () => {
        const reviewId = current.id;
        try {
          button.disabled = true;
          await api("/api/feedback", {
            review_id: reviewId,
            finding_id: finding.id,
            vote,
          });
          if (current?.id === reviewId) {
            current.feedback = {
              ...(current.feedback || {}),
              [finding.id]: vote,
            };
            renderFindings();
          }
          notice("Feedback saved locally. It does not retrain the model.");
        } catch (error) {
          notice(error.message, true);
          button.disabled = false;
        }
      });
      feedback.append(button);
    }
    card.append(feedback);
    list.append(card);
  }
}
function renderDiff(files) {
  const container = $("diff-files");
  container.replaceChildren();
  $("diff-preview").hidden = !files.length;
  for (const file of files) {
    const box = element("section", undefined, "diff-file");
    box.append(element("h4", file.path));
    const code = element("div", undefined, "diff-code");
    for (const line of file.lines) {
      const row = element("div", undefined, "diff-line " + line.kind);
      row.dataset.path = file.path;
      row.dataset.line = String(line.new_line || "");
      row.append(
        element(
          "span",
          line.old_line === null ? "" : line.old_line,
          "line-number",
        ),
        element(
          "span",
          line.new_line === null ? "" : line.new_line,
          "line-number",
        ),
        element(
          "span",
          { add: "+", delete: "-", context: " " }[line.kind] + line.text,
          "line-text",
        ),
      );
      code.append(row);
    }
    box.append(code);
    container.append(box);
  }
}
function highlight(path, line) {
  if ($("diff-preview").hidden) {
    notice(
      "Saved history retains findings and cited excerpts, but not the full input diff.",
    );
    return;
  }
  $("diff-preview").open = true;
  document.querySelectorAll(".diff-line").forEach((node) => {
    const matches =
      node.dataset.path === path && node.dataset.line === String(line);
    node.classList.toggle("highlight", matches);
    if (matches) node.scrollIntoView({ behavior: "smooth", block: "center" });
  });
}
document.querySelectorAll(".filter").forEach((button) =>
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    document
      .querySelectorAll(".filter")
      .forEach((node) => node.classList.toggle("active", node === button));
    if (current) renderFindings();
  }),
);
$("benchmark").addEventListener("click", async () => {
  try {
    setBusy(true, "Running benchmark…");
    notice(
      "Running labeled bug fixtures and clean controls using static checks…",
    );
    const result = await api("/api/evaluate", {});
    for (const id of ["precision", "recall", "f1"])
      $(id).textContent = (result[id] * 100).toFixed(1) + "%";
    $("clean").textContent = (result.clean_pass_rate * 100).toFixed(1) + "%";
    $("bench-mode").textContent = "STATIC · " + result.dataset_version;
    $("bench-meta").textContent =
      result.cases.length +
      " cases · " +
      result.true_positives +
      " true positives · " +
      result.false_positives +
      " false positives · " +
      result.false_negatives +
      " misses · " +
      result.latency_ms +
      " ms · " +
      new Date(result.evaluated_at).toLocaleString();
    const rows = $("case-rows");
    rows.replaceChildren();
    for (const test of result.cases) {
      const row = element("tr");
      const name = element("td");
      const details = element("details", undefined, "case-detail");
      details.append(element("summary", test.name));
      const expected =
        test.expected
          .map((f) => f.path + ":" + f.line + " / " + f.rule)
          .join("; ") || "No findings";
      const found =
        test.findings
          .map((f) => f.path + ":" + f.line + " / " + f.rule)
          .join("; ") || "No findings";
      details.append(
        element("p", "Expected: " + expected),
        element("p", "Observed: " + found),
      );
      if (test.warnings?.length)
        details.append(element("p", test.warnings.join(" ")));
      name.append(details);
      row.append(
        name,
        element("td", test.expected.length),
        element("td", test.findings.length),
        element(
          "td",
          test.passed ? "✓ Pass" : "× Miss / extra",
          test.passed ? "pass" : "fail",
        ),
        element("td", test.duration_ms + " ms"),
      );
      rows.append(row);
    }
    const categories = element("table");
    const categoryHead = element("tr");
    ["Rule", "TP / FP / FN", "Precision", "Recall"].forEach((text) =>
      categoryHead.append(element("th", text)),
    );
    categories.append(categoryHead);
    for (const category of result.per_category) {
      const row = element("tr");
      row.append(
        element("td", category.rule),
        element(
          "td",
          category.true_positives +
            " / " +
            category.false_positives +
            " / " +
            category.false_negatives,
        ),
        element("td", (category.precision * 100).toFixed(1) + "%"),
        element("td", (category.recall * 100).toFixed(1) + "%"),
      );
      categories.append(row);
    }
    $("category-results").replaceChildren(categories);
    $("bench-limitations").replaceChildren(
      ...result.limitations.map((text) => element("p", text, "microcopy")),
    );
    notice(
      "Benchmark complete. Expand each case to inspect exact expected and observed locations.",
    );
  } catch (error) {
    notice(error.message, true);
  } finally {
    setBusy(false);
  }
});
async function loadHistory() {
  try {
    const records = await api("/api/history");
    const list = $("history-list");
    list.replaceChildren();
    if (!records.length) {
      list.append(
        element(
          "p",
          "No saved reviews yet. Run a review to start your notebook.",
          "no-findings",
        ),
      );
      return;
    }
    for (const record of records) {
      const row = element("div", undefined, "history-row");
      const content = element("div");
      const title = element("button", record.title, "history-title");
      title.addEventListener("click", async () => {
        try {
          const result = await api("/api/reviews/" + record.id);
          show("review");
          renderResult(result);
          notice(
            "Saved review opened. Results and cited excerpts are retained; full source and diff are not.",
          );
        } catch (error) {
          notice(error.message, true);
        }
      });
      content.append(
        title,
        element(
          "p",
          new Date(record.created_at).toLocaleString() +
            " · " +
            record.findings +
            " findings · " +
            record.mode,
        ),
      );
      const actions = element("div", undefined, "history-actions");
      const exp = element("a", "Export ↗", "button small secondary");
      exp.href = "/api/reviews/" + record.id + "/export";
      exp.download = "patchpilot-review.md";
      const del = element("button", "Delete", "button small secondary delete");
      del.addEventListener("click", async () => {
        if (!confirm("Delete this local review and its feedback?")) return;
        try {
          await api("/api/delete", { review_id: record.id });
          if (current?.id === record.id) {
            current = null;
            $("results").hidden = true;
            $("results-empty").hidden = false;
            $("export").hidden = true;
          }
          await loadHistory();
          notice("Review and feedback deleted from local storage.");
        } catch (error) {
          notice(error.message, true);
        }
      });
      actions.append(exp, del);
      row.append(content, actions);
      list.append(row);
    }
  } catch (error) {
    notice(error.message, true);
  }
}
$("refresh-history").addEventListener("click", loadHistory);
api("/api/config")
  .then((config) => {
    token = config.token;
    $("model-label").textContent =
      config.model + " via local Ollama; static checks always run.";
  })
  .catch((error) => {
    setBusy(true, "Server unavailable");
    notice(error.message, true);
  });
