const state = {
  boxes: [],
  deleteName: null,
  deleteTrigger: null,
};

const elements = {
  createForm: document.querySelector("#create-form"),
  createError: document.querySelector("#create-error"),
  loading: document.querySelector("#loading-state"),
  empty: document.querySelector("#empty-state"),
  tableWrap: document.querySelector("#table-wrap"),
  rows: document.querySelector("#devbox-rows"),
  fleetSummary: document.querySelector("#fleet-summary"),
  readyCount: document.querySelector("#ready-count"),
  stoppedCount: document.querySelector("#stopped-count"),
  refreshButton: document.querySelector("#refresh-button"),
  logoutButton: document.querySelector("#logout-button"),
  deleteDialog: document.querySelector("#delete-dialog"),
  deleteName: document.querySelector("#delete-name"),
  purgeVolume: document.querySelector("#purge-volume"),
  confirmDelete: document.querySelector("#confirm-delete"),
  toastRegion: document.querySelector("#toast-region"),
};

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
  if (options.body) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-Devboxes-CSRF", decodeURIComponent(cookie("devboxes_csrf") || ""));
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
      if (Array.isArray(payload.detail)) {
        message = payload.detail.map((item) => item.msg).join(" · ");
      } else if (payload.detail) {
        message = payload.detail;
      }
    } catch (_) {
      // The HTTP status remains useful when an upstream response is not JSON.
    }
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function meta(text) {
  return node("span", "devbox-meta", text);
}

function actionButton(label, action, box, danger = false) {
  const button = node(
    "button",
    `row-action${danger ? " row-action-danger" : ""}`,
    label,
  );
  button.type = "button";
  button.dataset.action = action;
  button.dataset.name = box.name;
  return button;
}

function formatExpiry(box) {
  if (box.state === "stopped") return "Stopped safely";
  const difference = new Date(box.expires_at).getTime() - Date.now();
  if (difference <= 0) return "Stopping now";
  const hours = Math.ceil(difference / 3_600_000);
  if (hours <= 24) return `in ${hours} hour${hours === 1 ? "" : "s"}`;
  const days = Math.ceil(hours / 24);
  return `in ${days} day${days === 1 ? "" : "s"}`;
}

function renderBox(box) {
  const row = document.createElement("tr");

  const nameCell = document.createElement("th");
  nameCell.scope = "row";
  nameCell.append(node("span", "devbox-name", box.name));
  const details = [box.preset, box.storage_size];
  if (box.repository) details.push(box.repository);
  nameCell.append(meta(details.join(" · ")));

  const stateCell = document.createElement("td");
  stateCell.append(node("span", `status-chip status-${box.state}`, box.state));
  if (box.message) stateCell.append(meta(box.message));

  const workspaceCell = document.createElement("td");
  if (box.state === "stopped") {
    workspaceCell.append(node("span", "cell-note", "Home volume retained"));
  } else if (box.ssh_command) {
    workspaceCell.append(node("code", "ssh-command", box.ssh_command));
    workspaceCell.append(meta(box.restarts ? `${box.restarts} container restarts` : "tmux session main"));
  } else {
    workspaceCell.append(node("span", "cell-note", "SSH address pending"));
  }

  const expiryCell = document.createElement("td");
  expiryCell.append(node("span", "devbox-name", formatExpiry(box)));
  expiryCell.append(meta(new Date(box.expires_at).toLocaleString()));

  const actionsCell = document.createElement("td");
  const actions = node("div", "row-actions");
  if (box.ssh_command && box.state !== "stopped") {
    actions.append(actionButton("Copy SSH", "copy", box));
  }
  if (box.state === "stopped") {
    actions.append(actionButton("Start", "start", box));
  } else {
    actions.append(actionButton("Stop", "stop", box));
  }
  actions.append(actionButton("Delete", "delete", box, true));
  actionsCell.append(actions);

  row.append(nameCell, stateCell, workspaceCell, expiryCell, actionsCell);
  return row;
}

function focusedRowAction() {
  const active = document.activeElement;
  if (!(active instanceof HTMLButtonElement) || !elements.rows.contains(active)) return null;
  return { name: active.dataset.name, action: active.dataset.action };
}

function restoreRowAction(focused) {
  if (!focused?.name) return;
  const sameAction = elements.rows.querySelector(
    `button[data-name="${focused.name}"][data-action="${focused.action}"]`,
  );
  const lifecycleAction = elements.rows.querySelector(
    `button[data-name="${focused.name}"][data-action="start"], ` +
      `button[data-name="${focused.name}"][data-action="stop"]`,
  );
  const fallback = elements.rows.querySelector(`button[data-name="${focused.name}"]`);
  (sameAction || lifecycleAction || fallback)?.focus({ preventScroll: true });
}

function render() {
  const focused = focusedRowAction();
  elements.rows.replaceChildren(...state.boxes.map(renderBox));
  const ready = state.boxes.filter((box) => box.state === "ready").length;
  const stopped = state.boxes.filter((box) => box.state === "stopped").length;
  elements.readyCount.textContent = String(ready);
  elements.stoppedCount.textContent = String(stopped);
  elements.fleetSummary.textContent = state.boxes.length
    ? `${state.boxes.length} devbox${state.boxes.length === 1 ? "" : "es"} tracked by Kubernetes`
    : "No devboxes are currently provisioned";
  elements.empty.hidden = state.boxes.length !== 0;
  elements.tableWrap.hidden = state.boxes.length === 0;
  elements.loading.hidden = true;
  restoreRowAction(focused);
}

async function loadBoxes({ quiet = false } = {}) {
  if (!quiet) elements.refreshButton.disabled = true;
  try {
    const payload = await api("/api/v1/devboxes");
    state.boxes = payload.items;
    render();
  } catch (error) {
    toast(error.message, true);
  } finally {
    elements.refreshButton.disabled = false;
  }
}

async function perform(name, action) {
  const labels = { start: "Starting", stop: "Stopping" };
  toast(`${labels[action]} ${name}…`);
  try {
    await api(`/api/v1/devboxes/${encodeURIComponent(name)}/${action}`, { method: "POST" });
    await loadBoxes({ quiet: true });
    toast(`${name} ${action === "start" ? "is starting" : "stopped safely"}.`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard.");
  } catch (_) {
    toast("Clipboard access was denied.", true);
  }
}

function toast(message, error = false) {
  const item = node("div", `toast${error ? " toast-error" : ""}`, message);
  elements.toastRegion.append(item);
  window.setTimeout(() => item.remove(), 4200);
}

elements.createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.createError.hidden = true;
  const submit = elements.createForm.querySelector("button[type='submit']");
  const form = new FormData(elements.createForm);
  const payload = {
    name: form.get("name"),
    preset: form.get("preset"),
    ttl_hours: Number(form.get("ttl_hours")),
    repository: form.get("repository") || null,
  };
  submit.disabled = true;
  submit.setAttribute("aria-busy", "true");
  try {
    await api("/api/v1/devboxes", { method: "POST", body: JSON.stringify(payload) });
    elements.createForm.reset();
    toast(`${payload.name} is being prepared.`);
    await loadBoxes({ quiet: true });
  } catch (error) {
    elements.createError.textContent = error.message;
    elements.createError.hidden = false;
    elements.createError.focus();
  } finally {
    submit.disabled = false;
    submit.removeAttribute("aria-busy");
  }
});

elements.rows.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const box = state.boxes.find((item) => item.name === button.dataset.name);
  if (!box) return;
  if (button.dataset.action === "copy" && box.ssh_command) {
    copyText(box.ssh_command);
  } else if (["start", "stop"].includes(button.dataset.action)) {
    perform(box.name, button.dataset.action);
  } else if (button.dataset.action === "delete") {
    state.deleteName = box.name;
    state.deleteTrigger = button;
    elements.deleteName.textContent = box.name;
    elements.purgeVolume.checked = false;
    elements.deleteDialog.showModal();
  }
});

elements.confirmDelete.addEventListener("click", async (event) => {
  event.preventDefault();
  if (!state.deleteName) return;
  const name = state.deleteName;
  const purge = elements.purgeVolume.checked;
  elements.confirmDelete.disabled = true;
  try {
    const result = await api(
      `/api/v1/devboxes/${encodeURIComponent(name)}?purge=${purge}`,
      { method: "DELETE" },
    );
    elements.deleteDialog.close("deleted");
    state.deleteName = null;
    await loadBoxes({ quiet: true });
    elements.createForm.querySelector("#name").focus({ preventScroll: true });
    toast(result.message);
  } catch (error) {
    toast(error.message, true);
  } finally {
    elements.confirmDelete.disabled = false;
  }
});

elements.deleteDialog.addEventListener("close", () => {
  if (elements.deleteDialog.returnValue !== "deleted" && state.deleteTrigger?.isConnected) {
    state.deleteTrigger.focus({ preventScroll: true });
  }
  state.deleteName = null;
  state.deleteTrigger = null;
});

elements.refreshButton.addEventListener("click", () => loadBoxes());

elements.logoutButton.addEventListener("click", async () => {
  try {
    await api("/auth/logout", { method: "POST" });
  } finally {
    window.location.assign("/login");
  }
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-copy]");
  if (button) copyText(button.dataset.copy);
});

loadBoxes();
window.setInterval(() => loadBoxes({ quiet: true }), 8_000);
