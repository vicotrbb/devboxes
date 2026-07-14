const enabled = document.body.dataset.insightsEnabled === "true";
const logoutButton = document.querySelector("#logout-button");
const refreshButton = document.querySelector("#insights-refresh");

function cookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  return document.cookie
    .split("; ")
    .find((item) => item.startsWith(prefix))
    ?.slice(prefix.length);
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set(
      "X-Devboxes-CSRF",
      decodeURIComponent(cookie("devboxes_csrf") || ""),
    );
  }
  const response = await fetch(path, { ...options, method, headers });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Your session expired.");
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      // Preserve the status-based message for non-JSON upstream failures.
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

async function signOut() {
  try {
    await api("/auth/logout", { method: "POST" });
  } finally {
    window.location.assign("/login");
  }
}

logoutButton?.addEventListener("click", signOut);

if (!enabled) {
  refreshButton.disabled = true;
} else {
  const elements = {
    form: document.querySelector("#insights-filters"),
    range: document.querySelector("#insights-range"),
    sinceField: document.querySelector("#custom-since-field"),
    untilField: document.querySelector("#custom-until-field"),
    since: document.querySelector("#insights-since"),
    until: document.querySelector("#insights-until"),
    loading: document.querySelector("#insights-loading"),
    error: document.querySelector("#insights-error"),
    empty: document.querySelector("#insights-empty"),
    content: document.querySelector("#insights-content"),
    coverage: document.querySelector("#coverage-banner"),
    coverageCopy: document.querySelector("#coverage-copy"),
    sessions: document.querySelector("#metric-sessions"),
    tokens: document.querySelector("#metric-tokens"),
    cost: document.querySelector("#metric-cost"),
    active: document.querySelector("#metric-active"),
    aiLines: document.querySelector("#metric-ai-lines"),
    commits: document.querySelector("#metric-commits"),
    additions: document.querySelector("#metric-additions"),
    deletions: document.querySelector("#metric-deletions"),
    worktree: document.querySelector("#metric-worktree"),
    worktreeDetail: document.querySelector("#metric-worktree-detail"),
    providerTable: document.querySelector("#provider-table"),
    tokenChart: document.querySelector("#token-chart"),
    tokenTable: document.querySelector("#token-table"),
    activity: document.querySelector("#activity-list"),
    collectors: document.querySelector("#collector-list"),
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    if (text !== undefined) {
      element.textContent = text;
    }
    return element;
  }

  function formatInteger(value) {
    return new Intl.NumberFormat(undefined, {
      maximumFractionDigits: 0,
    }).format(Number(value || 0));
  }

  function formatMoney(value) {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 4,
    }).format(Number(value || 0));
  }

  function formatDuration(seconds) {
    const minutes = Math.round(Number(seconds || 0) / 60);
    if (minutes < 60) {
      return `${minutes}m`;
    }
    const hours = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
  }

  function isReported(value) {
    return value !== null && value !== undefined;
  }

  function query() {
    const form = new FormData(elements.form);
    const parameters = new URLSearchParams();
    const range = String(form.get("range"));
    if (range === "custom") {
      const since = elements.since.valueAsDate;
      const until = elements.until.valueAsDate;
      if (!since || !until) {
        throw new Error("Choose both ends of the custom range.");
      }
      parameters.set("since", since.toISOString());
      parameters.set("until", until.toISOString());
    } else {
      parameters.set("since", range);
    }
    for (const key of ["box", "provider", "model", "repo"]) {
      const value = String(form.get(key) || "").trim();
      if (value) {
        parameters.set(key, value);
      }
    }
    return parameters;
  }

  function renderCoverage(coverage) {
    elements.coverage.dataset.status = coverage.status;
    const collectorCount = coverage.collectors.length;
    if (coverage.status === "empty") {
      elements.coverageCopy.textContent =
        "Waiting for the first enabled workspace collector.";
      return;
    }
    const freshness = coverage.freshness_seconds;
    const age =
      freshness < 60 ? "under a minute" : `${Math.floor(freshness / 60)}m`;
    const qualification =
      coverage.status === "fresh"
        ? "complete"
        : coverage.status === "partial"
          ? "partial; inspect collector states below"
          : "stale";
    elements.coverageCopy.textContent = `${collectorCount} collector${collectorCount === 1 ? "" : "s"} · newest ${age} ago · coverage ${qualification}`;
  }

  function renderProviders(summary, capabilities) {
    const rows = [];
    for (const provider of ["codex", "claude"]) {
      const values = summary.providers[provider] || {
        sessions: 0,
        total_tokens: 0,
        models: [],
      };
      const row = document.createElement("tr");
      const name = document.createElement("th");
      name.scope = "row";
      name.textContent = provider === "codex" ? "Codex" : "Claude";
      const cost =
        capabilities[provider].cost.supported && isReported(values.cost_usd)
          ? formatMoney(values.cost_usd)
          : "Not reported";
      const active =
        capabilities[provider].active_time.supported &&
        isReported(values.active_seconds)
          ? formatDuration(values.active_seconds)
          : "Not reported";
      const tokenTypes = Object.entries(values.tokens || {})
        .map(([type, value]) => `${type} ${formatInteger(value)}`)
        .join(", ");
      for (const value of [
        name,
        node("td", "", formatInteger(values.sessions)),
        node("td", "", formatInteger(values.total_tokens)),
        node("td", "", tokenTypes || "Not reported"),
        node("td", "", cost),
        node("td", "", active),
        node(
          "td",
          "",
          values.models.length ? values.models.join(", ") : "No model data",
        ),
      ]) {
        row.append(value);
      }
      rows.push(row);
    }
    elements.providerTable.replaceChildren(...rows);
  }

  function renderSeries(items) {
    const maximum = Math.max(...items.map((item) => Number(item.value)), 1);
    const bars = items.map((item) => {
      const bar = node("div", `insights-bar bar-${item.provider}`);
      const amount = Math.max(2, (Number(item.value) / maximum) * 100);
      bar.style.setProperty("--bar-size", `${amount}%`);
      bar.setAttribute(
        "aria-label",
        `${item.provider}, ${new Date(item.bucket).toLocaleString()}, ${formatInteger(item.value)} tokens`,
      );
      bar.append(
        node("span", "bar-value", formatInteger(item.value)),
        node("span", "bar-label", item.provider),
      );
      return bar;
    });
    elements.tokenChart.replaceChildren(...bars);
    const rows = items.map((item) => {
      const row = document.createElement("tr");
      row.append(
        node("td", "", new Date(item.bucket).toLocaleString()),
        node("td", "", item.provider),
        node("td", "", formatInteger(item.value)),
      );
      return row;
    });
    elements.tokenTable.replaceChildren(...rows);
  }

  function renderActivity(items) {
    if (!items.length) {
      elements.activity.replaceChildren(
        node("li", "activity-empty", "No commits were observed in this range."),
      );
      return;
    }
    const entries = items.map((item) => {
      const entry = node("li", "activity-item");
      const heading = node("div", "activity-heading");
      heading.append(
        node("strong", "", item.repo),
        node("time", "", new Date(item.observed_at).toLocaleString()),
      );
      entry.append(
        heading,
        node(
          "span",
          "activity-detail",
          `${item.box} · +${formatInteger(item.additions)} −${formatInteger(item.deletions)} · ${formatInteger(item.files_changed)} file${item.files_changed === 1 ? "" : "s"}${item.is_merge ? " · merge" : ""}`,
        ),
      );
      return entry;
    });
    elements.activity.replaceChildren(...entries);
  }

  function renderCollectors(items) {
    if (!items.length) {
      elements.collectors.replaceChildren(
        node("li", "collector-empty", "No collector has checked in yet."),
      );
      return;
    }
    const entries = items.map((item) => {
      const entry = node("li", "collector-item");
      const heading = node("div", "collector-heading");
      heading.append(
        node("strong", "", `${item.box} · ${item.collector}`),
        node(
          "span",
          `status-chip status-${item.status}`,
          item.status.replaceAll("_", " "),
        ),
      );
      const dropped = item.dropped_points
        ? ` · ${formatInteger(item.dropped_points)} dropped points`
        : " · no known loss";
      entry.append(
        heading,
        node(
          "span",
          "collector-detail",
          `${formatInteger(item.queue_bytes)} queued bytes · ${formatInteger(item.freshness_seconds)}s old${dropped}`,
        ),
      );
      return entry;
    });
    elements.collectors.replaceChildren(...entries);
  }

  function render(summaryEnvelope, seriesEnvelope, activityEnvelope) {
    const data = summaryEnvelope.data;
    const totals = data.ai.totals;
    const code = data.code;
    elements.sessions.textContent = formatInteger(totals.sessions);
    elements.tokens.textContent = formatInteger(totals.tokens);
    elements.cost.textContent = !isReported(totals.provider_reported_cost_usd)
      ? "Not reported"
      : formatMoney(totals.provider_reported_cost_usd);
    elements.active.textContent = !isReported(totals.active_seconds)
      ? "Not reported"
      : formatDuration(totals.active_seconds);
    elements.aiLines.textContent = !isReported(totals.ai_lines)
      ? "Not reported"
      : formatInteger(totals.ai_lines);
    elements.commits.textContent = formatInteger(code.commits);
    elements.additions.textContent = formatInteger(code.additions);
    elements.deletions.textContent = formatInteger(code.deletions);
    const tree = code.working_tree;
    const changed = tree.staged_files + tree.unstaged_files;
    elements.worktree.textContent = changed
      ? `${formatInteger(changed)} files`
      : "Clean";
    elements.worktreeDetail.textContent = `${formatInteger(tree.staged_files)} staged · ${formatInteger(tree.unstaged_files)} unstaged tracked files`;
    renderCoverage(summaryEnvelope.coverage);
    renderProviders(data.ai, summaryEnvelope.capabilities);
    renderSeries(seriesEnvelope.data.items);
    renderActivity(activityEnvelope.data.items);
    renderCollectors(summaryEnvelope.coverage.collectors);
    const hasData =
      totals.sessions ||
      totals.tokens ||
      totals.provider_reported_cost_usd ||
      totals.active_seconds ||
      totals.ai_lines ||
      code.commits ||
      changed ||
      summaryEnvelope.coverage.collectors.length;
    elements.empty.hidden = Boolean(hasData);
    elements.content.hidden = !hasData;
    elements.loading.hidden = true;
  }

  async function load() {
    elements.loading.hidden = false;
    elements.error.hidden = true;
    refreshButton.disabled = true;
    try {
      const parameters = query();
      const seriesParameters = new URLSearchParams(parameters);
      seriesParameters.set("metric", "tokens");
      const [summary, series, activity] = await Promise.all([
        api(`/api/v1/insights/summary?${parameters}`),
        api(`/api/v1/insights/timeseries?${seriesParameters}`),
        api(`/api/v1/insights/activity?${parameters}`),
      ]);
      render(summary, series, activity);
    } catch (error) {
      elements.loading.hidden = true;
      elements.content.hidden = true;
      elements.empty.hidden = true;
      elements.error.textContent = error.message;
      elements.error.hidden = false;
      elements.error.focus();
    } finally {
      refreshButton.disabled = false;
    }
  }

  function toggleCustomRange() {
    const custom = elements.range.value === "custom";
    elements.sinceField.hidden = !custom;
    elements.untilField.hidden = !custom;
    elements.since.required = custom;
    elements.until.required = custom;
  }

  elements.range.addEventListener("change", toggleCustomRange);
  elements.form.addEventListener("submit", (event) => {
    event.preventDefault();
    load();
  });
  refreshButton.addEventListener("click", load);
  toggleCustomRange();
  load();
}
