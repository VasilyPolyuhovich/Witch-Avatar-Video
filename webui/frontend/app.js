const POLL_INTERVAL_MS = 3000;
const LONG_SESSION_WARNING_S = 30 * 60; // 30 minutes

const State = {
  NOT_STARTED: "not_started",
  CONNECTING: "connecting",
  READY: "ready",
  GENERATING: "generating",
};

let currentState = State.NOT_STARTED;
let pollTimer = null;

async function apiPost(path, options = {}) {
  return fetch(path, { method: "POST", ...options });
}

async function apiGet(path) {
  return fetch(path);
}

function formatElapsed(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}m ${s}s`;
}

function render() {
  document.getElementById("app-title").textContent = t("title");
  document.getElementById("page-title").textContent = t("title");
  document.getElementById("start-btn").textContent = t("start");
  document.getElementById("stop-btn").textContent = t("stop");
  document.getElementById("generate-btn").textContent = t("generate");
  document.getElementById("photo-label").textContent = t("photoLabel");
  document.getElementById("text-label").textContent = t("textLabel");
  document.getElementById("voice-label").textContent = t("voiceLabel");

  const statusKey = {
    [State.NOT_STARTED]: "statusNotStarted",
    [State.CONNECTING]: "statusConnecting",
    [State.READY]: "statusReady",
    [State.GENERATING]: "statusGenerating",
  }[currentState];
  document.getElementById("status-label").textContent = t(statusKey);

  document.getElementById("start-btn").disabled = currentState !== State.NOT_STARTED;
  document.getElementById("stop-btn").disabled = currentState === State.NOT_STARTED;
  document.getElementById("generate-form").style.display =
    (currentState === State.READY || currentState === State.GENERATING) ? "flex" : "none";
  document.getElementById("generate-btn").disabled = currentState !== State.READY;
}

function showWarning(message) {
  const banner = document.getElementById("warning-banner");
  banner.textContent = message;
  banner.style.display = "block";
}

function hideWarning() {
  document.getElementById("warning-banner").style.display = "none";
}

async function pollStatus() {
  const resp = await apiGet("/api/status");
  if (!resp.ok) return;
  const body = await resp.json();

  if (!body.active) {
    currentState = State.NOT_STARTED;
    stopPolling();
    render();
    return;
  }

  document.getElementById("elapsed-label").textContent =
    `${t("elapsedLabel")}: ${formatElapsed(body.elapsed_seconds)}`;

  if (body.elapsed_seconds > LONG_SESSION_WARNING_S) {
    showWarning(t("longSessionWarning"));
  } else {
    hideWarning();
  }

  if (currentState === State.CONNECTING && body.ready) {
    currentState = State.READY;
  }
  render();
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollStatus, POLL_INTERVAL_MS);
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

async function onStart() {
  currentState = State.CONNECTING;
  render();
  let resp;
  try {
    resp = await apiPost("/api/start");
  } catch (err) {
    console.error("Start request failed:", err);
    currentState = State.NOT_STARTED;
    render();
    alert(`${t("startFailed")}\n\n${err}`);
    return;
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail || detail;
    } catch (_) {
      // response body wasn't JSON -- fall back to statusText
    }
    console.error(`Start failed (HTTP ${resp.status}):`, detail);
    currentState = State.NOT_STARTED;
    render();
    alert(`${t("startFailed")}\n\n${detail}`);
    return;
  }
  startPolling();
}

async function onStop() {
  await apiPost("/api/stop");
  stopPolling();
  currentState = State.NOT_STARTED;
  hideWarning();
  render();
}

async function pollGenerationStatus(jobId) {
  // A network blip here must not be treated as job failure -- per spec's
  // Error handling section, keep retrying on the same interval rather
  // than propagating a fetch exception. Only an explicit `status:
  // "error"` response from the backend is a real failure.
  let body;
  try {
    const resp = await apiGet(`/api/generate/${jobId}/status`);
    body = await resp.json();
  } catch (e) {
    setTimeout(() => pollGenerationStatus(jobId), POLL_INTERVAL_MS);
    return;
  }

  if (body.status === "running") {
    setTimeout(() => pollGenerationStatus(jobId), POLL_INTERVAL_MS);
    return;
  }
  if (body.status === "error") {
    currentState = State.READY;
    render();
    alert(errorMessageFor(body.error_code));
    return;
  }
  const videoResp = await apiGet(`/api/generate/${jobId}/result`);
  const blob = await videoResp.blob();
  const video = document.getElementById("result-video");
  video.src = URL.createObjectURL(blob);
  document.getElementById("result-section").style.display = "block";
  currentState = State.READY;
  render();
}

async function onGenerate() {
  currentState = State.GENERATING;
  render();

  const formData = new FormData();
  formData.append("text", document.getElementById("text-input").value);
  formData.append("image", document.getElementById("photo-input").files[0]);
  const voiceFile = document.getElementById("voice-input").files[0];
  if (voiceFile) formData.append("voice", voiceFile);

  const resp = await apiPost("/api/generate", { body: formData });
  if (!resp.ok) {
    currentState = State.READY;
    render();
    alert(t("generateFailedGeneric"));
    return;
  }
  const { job_id } = await resp.json();
  pollGenerationStatus(job_id);
}

document.getElementById("start-btn").addEventListener("click", onStart);
document.getElementById("stop-btn").addEventListener("click", onStop);
document.getElementById("generate-btn").addEventListener("click", onGenerate);
document.getElementById("lang-en").addEventListener("click", () => { setLanguage("en"); render(); });
document.getElementById("lang-ua").addEventListener("click", () => { setLanguage("uk"); render(); });

render();
