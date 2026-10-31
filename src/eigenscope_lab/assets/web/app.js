"use strict";

const $ = (id) => document.getElementById(id);
const definitions = {
  robotics: {
    title: "Robotics",
    engine: "MuJoCo physics",
    description: "Watch a mobile robot navigate, stop for obstacles, and respond to sensor loss.",
    scope: "Actual MuJoCo positions, ray sensing and contacts",
  },
  telecom: {
    title: "Telecom",
    engine: "SimPy discrete-event engine",
    description: "Inspect subscriber queues, finite radio capacity, and the effect of overload.",
    scope: "Actual SimPy queue and throughput observations",
  },
  "oil-gas": {
    title: "Oil & Gas",
    engine: "CoolProp · SciPy · OPC UA",
    description:
      "Follow receiver pressure, reviewed load changes and independent shutdown protection.",
    scope: "Methane receiver model · Local OPC UA exchange",
  },
  provider: {
    title: "Model provider",
    engine: "OpenAI client · Loopback HTTP / SSE",
    description:
      "Exercise streamed responses, tool calls, timeouts, cancellation and malformed data.",
    scope: "Actual local HTTP/SSE · Deterministic model output",
  },
  tools: {
    title: "MCP tools",
    engine: "Official MCP SDK · Real subprocess",
    description:
      "Discover and call a local tool server, including an untrusted injected tool result.",
    scope: "Actual JSON-RPC stdio transport and schema checks",
  },
  coding: {
    title: "Coding workflow",
    engine: "Git worktrees · Python unittest",
    description:
      "Follow a failing test, an isolated code fix, passing tests and workspace cleanup.",
    scope: "Actual Git operations and bounded Python test commands",
  },
};
let selected = "robotics";
let state = { status: "idle", frames_received: 0, run_id: 0 };
let lastRun = 0;
let lastRevision = -1;
let serverGeneration = 0;
let frame = null;
let trace = [];
let activity = [];
let lastPhase = "";
let drawing = false;
let providerLatest = null;
let providerTimeline = [];
let providerDisconnectObserved = false;
const canvas = $("scene");
const ctx = canvas.getContext("2d");

function words(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replaceAll("-", " ");
}
function activityItem(text) {
  if (activity[0] === text) return;
  activity.unshift(text);
  activity = activity.slice(0, 9);
  $("activity").replaceChildren(
    ...activity.map((message) => {
      const li = document.createElement("li");
      li.textContent = message;
      return li;
    }),
  );
}
function showError(message) {
  $("feedback").textContent = message;
  $("feedback").hidden = !message;
}
function number(value, digits = 1) {
  return Number.isFinite(value) ? value.toFixed(digits) : "—";
}
function metrics(items) {
  $("metrics").replaceChildren(
    ...items.map(([label, value, note]) => {
      const box = document.createElement("div");
      box.className = "metric";
      const title = document.createElement("span");
      title.textContent = label;
      const amount = document.createElement("strong");
      amount.textContent = value;
      const caption = document.createElement("small");
      caption.textContent = note;
      box.append(title, amount, caption);
      return box;
    }),
  );
}
function observedChecks() {
  return frame?.observed_checks || state.result?.checks || [];
}
function observedCheck(name) {
  return observedChecks().find((check) => check.name === name);
}
function observedOutcome(name, success = "Passed") {
  const check = observedCheck(name);
  return !check ? "Awaiting observation" : check.passed ? success : "Needs attention";
}
function updateWorkflowEvidence() {
  const panel = $("workflow-output");
  const patch =
    observedCheck("patched_worktree_passes_contract")?.patch || state.result?.metrics?.patch;
  panel.hidden = selected !== "coding" || !patch;
  if (!panel.hidden) {
    const before = String(patch.before).split("\n"),
      after = String(patch.after).split("\n");
    let first = 0,
      lastBefore = before.length,
      lastAfter = after.length;
    while (first < lastBefore && first < lastAfter && before[first] === after[first]) first++;
    while (
      lastBefore > first &&
      lastAfter > first &&
      before[lastBefore - 1] === after[lastAfter - 1]
    ) {
      lastBefore--;
      lastAfter--;
    }
    $("workflow-caption").textContent = `Observed change · ${patch.file}`;
    $("workflow-diff").textContent = [
      ...before.slice(first, lastBefore).map((line) => "− " + line),
      ...after.slice(first, lastAfter).map((line) => "+ " + line),
    ].join("\n");
  }
}
function updateMetrics() {
  const questions = {
    robotics: "Can the robot reach its goal while respecting its stop limits?",
    telecom: "Can the allocated radio capacity serve this traffic without excessive queues?",
    "oil-gas": "Do proposals stay within operating limits, even when sensor data misleads?",
    provider: "When does useful content arrive, and does the stream finish or stop correctly?",
    tools:
      "Which tool actions were permitted, which were denied, and where did untrusted data enter?",
    coding: "Did the intended fix pass its tests while preserving the original checkout?",
  };
  $("decision-question").textContent = questions[selected];
  $("stage-title").textContent = {
    robotics: "Route and stopping envelope",
    telecom: "Radio load and service quality",
    "oil-gas": "Pressure, proposals and shutdown",
    provider: "Content arrival and stream lifecycle",
    tools: "Tool access and trust boundaries",
    coding: "Change, test and workspace isolation",
  }[selected];
  updateWorkflowEvidence();
  if (!frame) {
    metrics([
      ["Outcome", "Awaiting run", "Measurements appear after execution"],
      ["Selected scenario", definitions[selected].title, "Published sample data"],
      ["Playback", "Live measurements", "Display pacing is separate from simulation time"],
      ["Seed", $("seed").value, "Repeatable input"],
    ]);
  } else if (selected === "robotics") {
    metrics([
      [
        "Goal remaining",
        `${number(frame.goal_remaining_m, 2)} m`,
        `Goal tolerance ${number(frame.goal_tolerance_m, 2)} m`,
      ],
      [
        "Range above stop limit",
        `${number(frame.range_m - frame.stop_clearance_m, 2)} m`,
        `Ray range ${number(frame.range_m, 2)} m · stop at ${number(frame.stop_clearance_m, 2)} m`,
      ],
      [
        "Sensor age",
        `${number(frame.sensor_age_s * 1000, 0)} ms`,
        `Timeout ${number(frame.sensor_timeout_s * 1000, 0)} ms`,
      ],
      [
        "Motion / protection",
        frame.monitor_enabled === false
          ? "Control test"
          : frame.safety_stop
            ? "Stopped"
            : frame.goal_remaining_m <= frame.goal_tolerance_m
              ? "Goal reached"
              : "Moving",
        frame.monitor_enabled === false
          ? "Monitor deliberately bypassed"
          : `${number(frame.speed_mps, 2)} m/s · ${words(frame.stop_reason)}`,
      ],
    ]);
  } else if (selected === "telecom") {
    metrics([
      [
        "Offered / capacity",
        `${number(frame.offered_mbps, 0)} / ${number(frame.capacity_mbps, 0)} Mb/s`,
        frame.arrival_window_open
          ? "Configured traffic · arrivals active"
          : "Arrival window ended · draining queues",
      ],
      [
        "Observed p95 delay",
        `${number(frame.observed_latency_p95_ms, 2)} ms`,
        `${frame.latency_sample_count ?? 0} delivered packets · cumulative in phase`,
      ],
      [
        "Queue / drops",
        `${frame.queue_packets} / ${frame.dropped_packets}`,
        `Queued / dropped · limit ${frame.queue_limit_packets} per UE`,
      ],
      [
        "Service fairness",
        number(frame.jain_fairness, 3),
        "Jain index · 1 means equal delivered counts",
      ],
    ]);
  } else if (selected === "oil-gas") {
    metrics([
      [
        "Pressure / trip",
        `${number(frame.pressure_pa / 100000, 2)} / ${number(frame.hh_pressure_pa / 100000, 1)} bar(a)`,
        `Sensor reports ${number(frame.published_pressure_pa / 100000, 2)} bar(a)`,
      ],
      [
        "Requested / effective load",
        `${number(frame.requested_load * 100, 0)}% / ${number(frame.compressor_load * 100, 0)}%`,
        frame.safety_stop ? "Shutdown isolates inflow" : "Accepted command / effective drive",
      ],
      ["Sensor / action", `${number(frame.sensor_age_s, 2)} s old`, words(frame.action_status)],
      [
        "Protection",
        frame.safety_stop ? "Shutdown latched" : "Monitoring",
        words(frame.stop_reason),
      ],
    ]);
  } else if (selected === "provider" && providerLatest) {
    const first = providerTimeline.find((sample) => sample.event_type === "content");
    const finish = providerTimeline.find((sample) => sample.event_type === "finish");
    metrics([
      [
        "First content receipt",
        `${number(first?.elapsed_ms)} ms`,
        "First nonempty content · local HTTP + playback pace",
      ],
      [
        "Finish receipt",
        finish ? `${number(finish.elapsed_ms)} ms` : "Awaiting finish",
        "Observed finish event · local fixture",
      ],
      [
        "Content reconstructed",
        `${providerLatest.total_bytes} bytes`,
        `${providerLatest.chunk_index} SSE events · UTF-8 bytes, not tokens`,
      ],
      [
        "Cancel reached upstream",
        providerDisconnectObserved ? "Confirmed" : "Not yet observed",
        providerDisconnectObserved
          ? "Local server observed disconnect"
          : "Separate cancellation control",
      ],
    ]);
  } else if (selected === "tools") {
    metrics([
      [
        "Tool discovery",
        observedOutcome("stdio_handshake_and_tool_discovery", "1 registered tool"),
        "get_build_config · read-only fixture",
      ],
      [
        "Valid tool call",
        observedOutcome("real_tool_roundtrip", "Returned"),
        "Build target and LTO contract",
      ],
      [
        "Injected tool text",
        observedOutcome(
          "untrusted_injection_fixture_reaches_boundary",
          "Delivered as untrusted data",
        ),
        "Sanitization is tested separately in live mode",
      ],
      [
        "Subprocess cleanup",
        observedOutcome("server_process_reaped_after_session", "Exited"),
        "Observed after protocol session closes",
      ],
    ]);
  } else if (selected === "coding") {
    metrics([
      [
        "Baseline test",
        observedOutcome("baseline_test_exposes_real_bug", "Bug reproduced"),
        "Expected failure before the fix",
      ],
      [
        "Fixed worktree test",
        observedOutcome("patched_worktree_passes_contract", "Passed"),
        "Actual Python unittest execution",
      ],
      [
        "Original checkout",
        observedOutcome("original_checkout_unchanged", "Unchanged"),
        "Separate from the agent worktree",
      ],
      [
        "Workspace cleanup",
        observedOutcome("temporary_repository_removed", "Removed"),
        "Temporary repository and worktree",
      ],
    ]);
  }
}
function resetProvider() {
  providerLatest = null;
  providerTimeline = [];
  providerDisconnectObserved = false;
  $("provider-output").hidden = true;
  $("provider-text").textContent = "";
  $("provider-note").hidden = true;
}
function selectScenario(name) {
  if (!definitions[name]) return;
  selected = name;
  const definition = definitions[name];
  $("title").textContent = definition.title;
  $("description").textContent = definition.description;
  $("engine-name").textContent = definition.engine;
  $("scope").textContent = definition.scope;
  $("start").textContent = `Run ${definition.title.toLowerCase()}`;
  $("menu-toggle").textContent = `☰ Scenarios: ${definition.title}`;
  document
    .querySelectorAll("[data-scenario]")
    .forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.scenario === name)),
    );
  const menuWasOpen = $("scenario-menu").classList.contains("open");
  $("scenario-menu").classList.remove("open");
  $("menu-toggle").setAttribute("aria-expanded", "false");
  if (menuWasOpen) $("menu-toggle").focus();
  if (state.scenario !== name) {
    frame = null;
    trace = [];
    lastPhase = "";
    resetProvider();
    state = {
      ...state,
      scenario: name,
      status: "idle",
      frame: null,
      result: null,
      frames_received: 0,
      wall_ms: null,
      error: null,
    };
    $("phase").textContent = "Choose Run to begin.";
    $("telemetry").textContent = "No frame received.";
    $("run-status").textContent = "Ready";
    $("run-status").dataset.status = "idle";
    $("check-count").textContent = "Awaiting run";
    $("checks").replaceChildren();
    $("check-empty").hidden = false;
    $("download").hidden = true;
    $("frame-count").textContent = "0 frames received";
    $("reset").disabled = true;
    $("run-guidance").textContent =
      `Select Run ${definition.title.toLowerCase()} to begin. Sample data is included; no API key is needed.`;
    showError("");
  }
  updateMetrics();
  requestDraw();
}
function applyFrame(payload) {
  if (!payload || typeof payload !== "object") return;
  frame = payload;
  if (selected === "provider") {
    if (frame.phase === "streaming" && Number.isInteger(frame.chunk_index)) {
      providerLatest = frame;
      if (
        !providerTimeline.length ||
        frame.chunk_index > providerTimeline[providerTimeline.length - 1].chunk_index
      )
        providerTimeline.push(frame);
      providerTimeline = providerTimeline.slice(-128);
    }
    if (frame.stream?.latest) {
      providerLatest = frame.stream.latest;
      providerTimeline = frame.stream.timeline || [];
      providerDisconnectObserved = Boolean(frame.stream.disconnect_observed);
    }
    if (frame.phase === "upstream_disconnect" && frame.observed === true)
      providerDisconnectObserved = true;
    if (providerLatest) {
      $("provider-output").hidden = false;
      $("provider-text").textContent =
        providerLatest.text_preview || "Waiting for response content…";
      $("provider-note").hidden = !providerDisconnectObserved;
      $("provider-note").textContent =
        "Cancellation control: upstream disconnect observed by the local HTTP server.";
    }
  }
  if (lastPhase !== frame.phase) {
    lastPhase = frame.phase;
    trace = [];
    activityItem(`Observed: ${words(frame.phase)}.`);
  }
  trace.push(frame);
  if (trace.length > 600) trace.shift();
  $("phase").textContent =
    selected === "oil-gas" && frame.phase === "stale-sensor-stop"
      ? "Separate receiver experiment · stale-sensor stop"
      : words(frame.phase || "Engine running");
  $("telemetry").textContent = JSON.stringify(frame, null, 2);
  updateMetrics();
  requestDraw();
}
function renderState(next, { reset = false, revision = next.event_id } = {}) {
  if (!Number.isSafeInteger(revision) || revision < 0) return;
  if (reset) {
    serverGeneration += 1;
    lastRevision = -1;
    lastRun = -1;
  }
  if (revision < lastRevision) return;
  if (revision === lastRevision) {
    // A delayed HTTP response must not undo a terminal SSE state. Equal
    // revisions can advance to a terminal state, but never regress from one.
    const rank = { idle: 0, running: 1, cancelling: 2, completed: 3, cancelled: 3, failed: 3 };
    if (next.run_id !== state.run_id || rank[next.status] <= rank[state.status]) return;
  }
  lastRevision = revision;
  if (next.run_id !== lastRun) {
    lastRun = next.run_id;
    trace = [];
    frame = null;
    activity = [];
    lastPhase = "";
    resetProvider();
    activityItem(`Run ${next.run_id} started with seed ${next.seed ?? 7}.`);
  }
  state = next;
  if (next.scenario && next.scenario !== selected) selectScenario(next.scenario);
  const busy = ["running", "cancelling"].includes(state.status);
  $("start").disabled = busy;
  $("cancel").disabled = !busy || state.status === "cancelling";
  $("cancel").textContent = state.status === "cancelling" ? "Stopping…" : "Stop";
  $("reset").disabled = busy || state.status === "idle";
  $("start").textContent = ["completed", "cancelled", "failed"].includes(state.status)
    ? "Run again"
    : `Run ${definitions[selected].title.toLowerCase()}`;
  $("seed").disabled = busy;
  $("pacing").disabled = busy;
  document.querySelectorAll("[data-scenario]").forEach((button) => {
    button.disabled = busy;
  });
  const labels = {
    idle: "Ready",
    running: "Running",
    cancelling: "Stopping",
    cancelled: "Stopped",
    completed: "Passed",
    failed: "Needs attention",
  };
  $("run-status").textContent = labels[state.status] || "Ready";
  $("run-status").dataset.status = state.status;
  $("frame-count").textContent = `${state.frames_received || 0} frames received`;
  if (next.frame) applyFrame(next.frame);
  else if (state.status === "running")
    $("phase").textContent = "Starting engine · Waiting for the first observation…";
  else if (state.status === "cancelling")
    $("phase").textContent = "Stopping at the next engine checkpoint…";
  else if (state.status === "cancelled")
    $("phase").textContent = "Run stopped before any measurement was received.";
  else if (state.status === "failed")
    $("phase").textContent = "The run ended before its first measurement.";
  if (state.status === "idle") {
    frame = null;
    trace = [];
    resetProvider();
    $("phase").textContent = "Choose Run to begin.";
    $("telemetry").textContent = "No frame received.";
  }
  const checks = state.result?.checks || [];
  const passedCount = checks.filter((check) => check.passed).length;
  const guidance = {
    idle: `Select Run ${definitions[selected].title.toLowerCase()} to begin. Sample data is included; no API key is needed.`,
    running:
      "Running locally. Stop ends the run at the next checkpoint and cleans up its resources.",
    cancelling: "Stopping… waiting for the current operation and cleanup to finish.",
    cancelled: "Run stopped. Select Run again to complete all scenario checks.",
    completed: `All ${checks.length} checks passed. Review the results below or download the run report.`,
    failed: checks.length
      ? `${checks.length - passedCount} of ${checks.length} checks need attention. Review the results below or download the report.`
      : "The run could not finish. Check the error message and local terminal, then try again.",
  };
  $("run-guidance").textContent = guidance[state.status] || guidance.idle;
  $("check-empty").textContent = busy
    ? "Checks will appear when the run finishes."
    : state.status === "cancelled"
      ? "This run stopped before its checks finished. Run again to see complete results."
      : "Run this scenario to see which checks pass and which need attention.";
  $("check-empty").hidden = checks.length > 0;
  $("check-count").textContent = checks.length
    ? `${checks.filter((c) => c.passed).length} / ${checks.length} passed`
    : busy
      ? "In progress"
      : "Awaiting run";
  $("checks").replaceChildren(
    ...checks.map((check) => {
      const li = document.createElement("li"),
        mark = document.createElement("span"),
        text = document.createElement("span");
      mark.className = `check-mark${check.passed ? "" : " failed"}`;
      mark.textContent = check.passed ? "✓" : "×";
      text.textContent = words(check.name);
      li.append(mark, text);
      return li;
    }),
  );
  $("download").hidden = !state.result;
  if (["completed", "cancelled", "failed"].includes(state.status))
    activityItem(`Run ${state.run_id} ${labels[state.status].toLowerCase()}.`);
  if (state.error) showError(state.error);
  const history = state.history || [];
  if (history.length)
    $("history").replaceChildren(
      ...history.slice(0, 5).map((run) => {
        const li = document.createElement("li"),
          name = document.createElement("strong"),
          detail = document.createElement("span");
        name.textContent = definitions[run.scenario]?.title || run.scenario;
        detail.textContent = `${labels[run.status] || run.status} · Seed ${run.seed} · ${number(run.wall_ms / 1000, 2)} s`;
        li.append(name, detail);
        return li;
      }),
    );
  updateMetrics();
  requestDraw();
}
async function action(path, body) {
  showError("");
  const generation = serverGeneration;
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (generation !== serverGeneration) return;
    if (!response.ok) throw new Error(data.error || "This action could not complete. Try again.");
    renderState(data);
  } catch (error) {
    if (generation !== serverGeneration) return;
    showError(
      error instanceof TypeError
        ? "The local lab is unavailable. Check its terminal and reconnect."
        : error.message,
    );
  }
}
$("start").addEventListener("click", () => {
  const seed = Number($("seed").value);
  if (!$("seed").value.trim() || !Number.isInteger(seed) || seed < 0 || seed > 4294967295) {
    showError("Enter a whole-number seed from 0 to 4294967295.");
    $("seed").focus();
    return;
  }
  action("/api/run", { scenario: selected, seed, pace_ms: Number($("pacing").value) });
});
$("cancel").addEventListener("click", () => action("/api/cancel", {}));
$("reset").addEventListener("click", () => action("/api/reset", {}));
document
  .querySelectorAll("[data-scenario]")
  .forEach((button) =>
    button.addEventListener("click", () => selectScenario(button.dataset.scenario)),
  );
$("menu-toggle").addEventListener("click", () => {
  const open = $("scenario-menu").classList.toggle("open");
  $("menu-toggle").setAttribute("aria-expanded", String(open));
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && $("scenario-menu").classList.contains("open")) {
    $("scenario-menu").classList.remove("open");
    $("menu-toggle").setAttribute("aria-expanded", "false");
    $("menu-toggle").focus();
  }
});
let theme =
  localStorage.getItem("eigenscope-lab-theme") ||
  (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
function applyTheme() {
  document.documentElement.dataset.theme = theme;
  $("theme").textContent = theme === "dark" ? "Light mode" : "Dark mode";
  requestDraw();
}
$("theme").addEventListener("click", () => {
  theme = theme === "dark" ? "light" : "dark";
  localStorage.setItem("eigenscope-lab-theme", theme);
  applyTheme();
});
$("download").addEventListener("click", () => {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(state, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `eigenscope-lab-${selected}-${state.run_id}.json`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
function requestDraw() {
  if (!drawing) {
    drawing = true;
    requestAnimationFrame(() => {
      drawing = false;
      draw();
    });
  }
}
function color(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function line(x1, y1, x2, y2, stroke, width = 2) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.strokeStyle = stroke;
  ctx.lineWidth = width;
  ctx.stroke();
}
function text(value, x, y, size = 16, fill = color("--muted"), align = "left") {
  ctx.font = `500 ${size}px ui-sans-serif,system-ui,sans-serif`;
  ctx.fillStyle = fill;
  ctx.textAlign = align;
  ctx.fillText(value, x, y);
}
function circle(x, y, radius, fill, stroke) {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, 2 * Math.PI);
  ctx.fillStyle = fill;
  ctx.fill();
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2;
    ctx.stroke();
  }
}
function draw() {
  const compact = canvas.clientWidth < 650;
  const desiredWidth = compact ? 640 : 1080;
  const workflow = selected === "tools" || selected === "coding";
  const desiredHeight = compact ? 440 : workflow ? 340 : 500;
  if (canvas.width !== desiredWidth || canvas.height !== desiredHeight) {
    canvas.width = desiredWidth;
    canvas.height = desiredHeight;
  }
  canvas.classList.toggle("compact", compact);
  canvas.classList.toggle("workflow", workflow && !compact);
  const W = canvas.width,
    H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = color("--surface-soft");
  ctx.fillRect(0, 0, W, H);
  for (let x = 30; x < W; x += 50) line(x, 0, x, H, color("--grid"), 1);
  for (let y = 25; y < H; y += 50) line(0, y, W, y, color("--grid"), 1);
  if (!frame) {
    text(
      compact ? "Run a scenario to see its" : "Run a scenario to see its actual engine state",
      W / 2,
      H / 2,
      compact ? 27 : 22,
      color("--muted"),
      "center",
    );
    if (compact) text("actual engine state", W / 2, H / 2 + 42, 27, color("--muted"), "center");
    canvas.setAttribute("aria-label", "Awaiting a simulation run. No telemetry received.");
    return;
  }
  if (selected === "oil-gas") {
    drawOilGas(compact);
    return;
  }
  if (selected === "tools" || selected === "coding") {
    drawWorkflow(compact);
    return;
  }
  if (compact) {
    drawCompact();
    return;
  }
  if (selected === "robotics") drawRobot();
  else if (selected === "telecom") drawTelecom();
  else if (selected === "provider" && providerLatest) drawProvider(false);
  else drawContract();
}
function drawOilGas(compact) {
  const W = canvas.width,
    ink = color("--ink"),
    muted = color("--muted");
  const accent = color("--accent"),
    danger = color("--danger");
  const stopped = Boolean(frame.safety_stop),
    cy = compact ? 145 : 155;
  const inlet = compact ? 90 : 170,
    receiver = W / 2,
    outlet = compact ? W - 90 : W - 170;
  text("GAS COMPRESSION · DISCHARGE RECEIVER", 28, 38, compact ? 23 : 21, ink);
  text(
    `${frame.phase === "stale-sensor-stop" ? "New receiver · stale-sensor test" : words(frame.phase)} · ${number(frame.t, 1)} s simulated`,
    28,
    73,
    compact ? 24 : 19,
    muted,
  );
  line(inlet + 32, cy, receiver - 80, cy, stopped ? muted : accent, 5);
  line(receiver + 80, cy, outlet - 30, cy, frame.outlet_opening > 0.1 ? accent : danger, 5);
  circle(
    inlet,
    cy,
    31,
    stopped ? color("--danger-soft") : color("--accent-soft"),
    stopped ? danger : accent,
  );
  text(stopped ? "×" : "›", inlet, cy + 13, 42, stopped ? danger : accent, "center");
  ctx.fillStyle = color("--surface");
  ctx.strokeStyle = stopped ? danger : accent;
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.roundRect(receiver - 79, cy - 55, 158, 110, 26);
  ctx.fill();
  ctx.stroke();
  text(number(frame.pressure_pa / 100000, 2), receiver, cy + 7, compact ? 40 : 43, ink, "center");
  text("bar(a)", receiver, cy + 37, 23, muted, "center");
  circle(
    outlet,
    cy,
    23,
    frame.outlet_opening > 0.1 ? color("--accent-soft") : color("--danger-soft"),
    frame.outlet_opening > 0.1 ? accent : danger,
  );
  text("Compressor", inlet, cy + 83, compact ? 25 : 20, ink, "center");
  text("Receiver", receiver, cy + 83, compact ? 25 : 20, ink, "center");
  text(
    `Outlet · ${number(frame.outlet_opening * 100, 0)}%`,
    outlet,
    cy + 83,
    compact ? 25 : 20,
    ink,
    "center",
  );
  const x = 45,
    y = compact ? 290 : 302,
    width = W - 90,
    height = compact ? 90 : 125;
  const ceiling = Math.max(frame.hh_pressure_pa * 1.15, frame.pressure_pa * 1.1);
  const py = (pressure) => y + height - Math.max(0, Math.min(1, pressure / ceiling)) * height;
  line(x, y + height, x + width, y + height, muted);
  ctx.save();
  ctx.setLineDash([7, 6]);
  line(x, py(frame.hh_pressure_pa), x + width, py(frame.hh_pressure_pa), danger, 2);
  ctx.restore();
  text(
    `High-high trip ${number(frame.hh_pressure_pa / 100000, 1)} bar(a)`,
    x,
    y - 13,
    compact ? 23 : 18,
    danger,
  );
  trace.forEach((sample, i) => {
    if (!i) return;
    line(
      x + ((i - 1) / Math.max(1, trace.length - 1)) * width,
      py(trace[i - 1].pressure_pa),
      x + (i / Math.max(1, trace.length - 1)) * width,
      py(sample.pressure_pa),
      color("--chart"),
      3,
    );
  });
  ctx.save();
  ctx.setLineDash([4, 5]);
  trace.forEach((sample, i) => {
    if (i)
      line(
        x + ((i - 1) / Math.max(1, trace.length - 1)) * width,
        py(trace[i - 1].published_pressure_pa),
        x + (i / Math.max(1, trace.length - 1)) * width,
        py(sample.published_pressure_pa),
        color("--warning"),
        2,
      );
  });
  ctx.restore();
  const tripIndex = trace.findIndex((sample) => sample.safety_stop);
  if (tripIndex >= 0)
    circle(
      x + (tripIndex / Math.max(1, trace.length - 1)) * width,
      py(trace[tripIndex].trip_pressure_pa ?? trace[tripIndex].pressure_pa),
      5,
      danger,
    );
  text("Truth", x + width - 180, y + height - 10, compact ? 21 : 15, color("--chart"));
  text("Sensor", x + width - 22, y + height - 10, compact ? 21 : 15, color("--warning"), "right");
  text(
    stopped
      ? `Shutdown latched · ${words(frame.stop_reason)}`
      : "Independent pressure protection active",
    x,
    compact ? 420 : 470,
    compact ? 23 : 20,
    stopped ? danger : accent,
  );
  canvas.setAttribute(
    "aria-label",
    `Gas receiver ${words(frame.phase)}, pressure ${number(frame.pressure_pa / 100000, 2)} bar absolute; trip ${number(frame.hh_pressure_pa / 100000, 1)} bar; inlet ${number(frame.inflow_kg_s, 3)} kilograms per second, outlet ${number(frame.outflow_kg_s, 3)}; safety stop ${stopped}.`,
  );
}
function drawCompact() {
  const ink = color("--ink"),
    muted = color("--muted"),
    accent = color("--accent");
  if (selected === "robotics") {
    const scale = 140,
      px = (x) => 65 + x * scale,
      py = (y) => 230 - y * scale;
    text(words(frame.phase), 28, 44, 27, ink);
    text("Actual position · metres", 28, 82, 23, muted);
    const obstacle = frame.obstacle;
    if (obstacle) {
      ctx.fillStyle = color("--danger-soft");
      ctx.strokeStyle = color("--danger");
      ctx.lineWidth = 3;
      ctx.fillRect(
        px(obstacle.x - obstacle.half_x),
        py(obstacle.y + obstacle.half_y),
        2 * obstacle.half_x * scale,
        2 * obstacle.half_y * scale,
      );
      ctx.strokeRect(
        px(obstacle.x - obstacle.half_x),
        py(obstacle.y + obstacle.half_y),
        2 * obstacle.half_x * scale,
        2 * obstacle.half_y * scale,
      );
    }
    if (Number.isFinite(frame.goal_x)) {
      circle(px(frame.goal_x), py(frame.goal_y), 17, color("--accent-soft"), accent);
      text("Goal", px(frame.goal_x), py(frame.goal_y) + 49, 24, accent, "center");
    }
    for (let i = 1; i < trace.length; i++)
      line(
        px(trace[i - 1].x),
        py(trace[i - 1].y),
        px(trace[i].x),
        py(trace[i].y),
        color("--chart"),
        4,
      );
    const x = px(frame.x),
      y = py(frame.y),
      yaw = frame.yaw || 0;
    circle(
      x,
      y,
      22,
      frame.safety_stop ? color("--danger-soft") : color("--accent-soft"),
      frame.safety_stop ? color("--danger") : accent,
    );
    line(x, y, x + Math.cos(yaw) * 31, y - Math.sin(yaw) * 31, ink, 4);
    text(
      `x ${number(frame.x, 2)} · y ${number(frame.y, 2)} · ${number(frame.t, 2)} s`,
      28,
      363,
      25,
      ink,
    );
    text(
      frame.monitor_enabled === false
        ? "Collision control · monitor bypassed"
        : frame.safety_stop
          ? `Safety stop: ${words(frame.stop_reason)}`
          : `Goal remaining ${number(frame.goal_remaining_m, 2)} m`,
      28,
      405,
      24,
      frame.safety_stop ? color("--danger") : accent,
    );
    canvas.setAttribute(
      "aria-label",
      `Robot ${words(frame.phase)}, x ${number(frame.x, 2)}, y ${number(frame.y, 2)}, safety stop ${Boolean(frame.safety_stop)}.`,
    );
  } else if (selected === "telecom") {
    text(`Subscriber queues · ${words(frame.phase)}`, 28, 43, 25, ink);
    const queues = frame.per_ue_queue_packets || [0, 0, 0, 0],
      maximum = Math.max(1, frame.queue_limit_packets || 1);
    queues.forEach((value, i) => {
      const y = 94 + i * 58;
      text(`UE ${i + 1}`, 28, y + 6, 24, ink);
      ctx.fillStyle = color("--grid");
      ctx.fillRect(130, y - 14, 365, 24);
      ctx.fillStyle = color("--chart");
      ctx.fillRect(130, y - 14, (value / maximum) * 365, 24);
      text(`${value}/${frame.queue_limit_packets}`, 510, y + 6, 22, ink);
    });
    text(
      `${number(frame.throughput_mbps)} / ${number(frame.capacity_mbps)} Mb/s`,
      28,
      350,
      26,
      ink,
    );
    text(
      `${frame.allocated_prbs} PRBs · ${frame.bandwidth_mhz} MHz · ${number(frame.t_seconds, 2)} s`,
      28,
      401,
      23,
      muted,
    );
    canvas.setAttribute(
      "aria-label",
      `Telecom ${words(frame.phase)}, subscriber queues ${queues.join(", ")}, throughput ${number(frame.throughput_mbps)} megabits per second.`,
    );
  } else if (selected === "provider" && providerLatest) {
    drawProvider(true);
  } else {
    const names =
      selected === "provider"
        ? ["Client", "HTTP / SSE", "Provider"]
        : selected === "tools"
          ? ["Client", "JSON-RPC", "Tool server"]
          : ["Contract", "Worktree", "Tests"];
    line(120, 140, 520, 140, color("--line"), 3);
    names.forEach((name, i) => {
      const x = 120 + i * 200;
      circle(x, 140, 40, color("--surface"), accent);
      text(String(i + 1), x, 149, 29, accent, "center");
      text(name, x, 218, 24, ink, "center");
    });
    text(`${frame.completed_checks || 0} checks observed`, 320, 306, 29, ink, "center");
    const label = words(frame.phase),
      parts = label.split(" ");
    let current = "",
      lines = [];
    for (const part of parts) {
      if ((current + " " + part).length > 35) {
        lines.push(current);
        current = part;
      } else current = current ? current + " " + part : part;
    }
    if (current) lines.push(current);
    lines
      .slice(0, 2)
      .forEach((value, i) =>
        text(value, 320, 362 + i * 35, 24, frame.passed ? accent : color("--danger"), "center"),
      );
    canvas.setAttribute(
      "aria-label",
      `${definitions[selected].title}, ${frame.completed_checks || 0} checks observed, ${label}.`,
    );
  }
}
function drawProvider(compact) {
  const ink = color("--ink"),
    muted = color("--muted"),
    accent = color("--accent");
  const W = canvas.width,
    chartX = compact ? 50 : 80,
    chartY = compact ? 132 : 180;
  const chartW = compact ? 540 : 920,
    chartH = compact ? 180 : 205;
  if (compact) {
    text("Actual SSE stream", 28, 43, 28, ink);
    text(
      `${providerLatest.chunk_index} chunks · ${providerLatest.total_bytes} bytes`,
      28,
      88,
      25,
      ink,
    );
  } else {
    const names = ["OpenAI SDK client", "HTTP / SSE transport", "Local provider"];
    [160, 540, 920].forEach((x, i) => {
      if (i > 0) line(x - 335, 67, x - 45, 67, color("--line"), 3);
      circle(x, 67, 30, color("--surface"), accent);
      text(String(i + 1), x, 74, 21, accent, "center");
      text(names[i], x, 124, 17, ink, "center");
    });
    text("Content bytes received over actual streamed events", chartX, 158, 16, ink);
  }
  const samples = providerTimeline,
    maxTime = Math.max(1, ...samples.map((sample) => sample.elapsed_ms)),
    maxBytes = Math.max(1, providerLatest.total_bytes);
  line(chartX, chartY, chartX, chartY + chartH, color("--line"), 2);
  line(chartX, chartY + chartH, chartX + chartW, chartY + chartH, color("--line"), 2);
  samples.forEach((sample, index) => {
    const x = chartX + (sample.elapsed_ms / maxTime) * chartW,
      y = chartY + chartH - (sample.total_bytes / maxBytes) * chartH;
    if (index > 0) {
      const previous = samples[index - 1];
      line(
        chartX + (previous.elapsed_ms / maxTime) * chartW,
        chartY + chartH - (previous.total_bytes / maxBytes) * chartH,
        x,
        y,
        color("--chart"),
        compact ? 4 : 3,
      );
    }
    circle(
      x,
      y,
      sample.event_type === "finish" ? 6 : 3,
      sample.event_type === "finish" ? accent : color("--chart"),
    );
  });
  text("0", chartX - 12, chartY + chartH + (compact ? 31 : 25), compact ? 23 : 13, muted);
  text(
    `${number(maxTime)} ms`,
    chartX + chartW,
    chartY + chartH + (compact ? 31 : 25),
    compact ? 23 : 13,
    muted,
    "right",
  );
  const label = providerLatest.stream_finished
    ? "Finish event received"
    : "Receiving actual stream events";
  text(label, compact ? 28 : 80, compact ? 405 : 463, compact ? 25 : 18, accent);
  if (!compact)
    text(
      `${providerLatest.chunk_index} chunks · ${providerLatest.total_bytes} bytes`,
      W - 80,
      463,
      17,
      ink,
      "right",
    );
  canvas.setAttribute(
    "aria-label",
    `Provider stream: ${providerLatest.chunk_index} actual SSE chunks, ${providerLatest.total_bytes} UTF-8 content bytes, elapsed ${number(providerLatest.elapsed_ms)} milliseconds including visual pacing, ${label.toLowerCase()}.`,
  );
}
function drawRobot() {
  const scale = 152,
    px = (x) => 130 + x * scale,
    py = (y) => 270 - y * scale;
  text("OVERHEAD VIEW · metres", 30, 35, 14);
  text(`Phase: ${words(frame.phase)}`, 30, 62, 16, color("--ink"));
  const obstacle = frame.obstacle;
  if (obstacle) {
    ctx.fillStyle = color("--danger-soft");
    ctx.strokeStyle = color("--danger");
    ctx.lineWidth = 2;
    ctx.fillRect(
      px(obstacle.x - obstacle.half_x),
      py(obstacle.y + obstacle.half_y),
      2 * obstacle.half_x * scale,
      2 * obstacle.half_y * scale,
    );
    ctx.strokeRect(
      px(obstacle.x - obstacle.half_x),
      py(obstacle.y + obstacle.half_y),
      2 * obstacle.half_x * scale,
      2 * obstacle.half_y * scale,
    );
    text(
      "Obstacle",
      px(obstacle.x),
      py(obstacle.y + obstacle.half_y) - 12,
      14,
      color("--danger"),
      "center",
    );
  }
  if (Number.isFinite(frame.goal_x)) {
    circle(px(frame.goal_x), py(frame.goal_y), 16, color("--accent-soft"), color("--accent"));
    text("Goal", px(frame.goal_x), py(frame.goal_y) + 37, 14, color("--accent"), "center");
  }
  for (let i = 1; i < trace.length; i++)
    line(
      px(trace[i - 1].x),
      py(trace[i - 1].y),
      px(trace[i].x),
      py(trace[i].y),
      color("--chart"),
      3,
    );
  const x = px(frame.x),
    y = py(frame.y),
    yaw = frame.yaw || 0;
  if (Number.isFinite(frame.range_m))
    line(
      x,
      y,
      x + Math.cos(yaw) * Math.min(frame.range_m, 4) * scale,
      y - Math.sin(yaw) * Math.min(frame.range_m, 4) * scale,
      color("--muted"),
      1.5,
    );
  circle(
    x,
    y,
    22,
    frame.safety_stop ? color("--danger-soft") : color("--accent-soft"),
    frame.safety_stop ? color("--danger") : color("--accent"),
  );
  line(x, y, x + Math.cos(yaw) * 30, y - Math.sin(yaw) * 30, color("--ink"), 4);
  text(
    `x ${number(frame.x, 2)} · y ${number(frame.y, 2)}`,
    x,
    y + 50,
    14,
    color("--ink"),
    "center",
  );
  const panelX = 650;
  text("GOAL & PROTECTION", panelX, 100, 17, color("--ink"));
  text(`${number(frame.goal_remaining_m, 2)} m to goal`, panelX, 150, 27, color("--ink"));
  text(`Tolerance ${number(frame.goal_tolerance_m, 2)} m`, panelX, 184, 16);
  text(
    `Range ${number(frame.range_m, 2)} m / stop ${number(frame.stop_clearance_m, 2)} m`,
    panelX,
    241,
    21,
    color("--ink"),
  );
  text(
    `Sensor ${number(frame.sensor_age_s * 1000, 0)} / ${number(frame.sensor_timeout_s * 1000, 0)} ms`,
    panelX,
    287,
    21,
    color("--ink"),
  );
  text(
    frame.monitor_enabled === false
      ? "COLLISION CONTROL · MONITOR BYPASSED"
      : "Independent monitor enabled",
    panelX,
    343,
    16,
    frame.monitor_enabled === false ? color("--warning") : color("--accent"),
  );
  text(`Contacts ${frame.contacts ?? 0}`, 30, 455, 16);
  text(
    frame.monitor_enabled === false
      ? "Collision control · monitor bypassed"
      : frame.safety_stop
        ? `Safety stop · ${words(frame.stop_reason)}`
        : "Controller active",
    1050,
    455,
    16,
    frame.safety_stop ? color("--danger") : color("--accent"),
    "right",
  );
  canvas.setAttribute(
    "aria-label",
    `Robotics phase ${words(frame.phase)}, position ${number(frame.x, 2)}, ${number(frame.y, 2)} metres, speed ${number(frame.speed_mps, 2)} metres per second, safety stop ${Boolean(frame.safety_stop)}.`,
  );
}
function drawTelecom() {
  text("SUBSCRIBER QUEUES → RADIO CAPACITY", 30, 35, 14);
  const queues = frame.per_ue_queue_packets || [0, 0, 0, 0],
    maxQueue = Math.max(1, frame.queue_limit_packets || 1);
  queues.forEach((queue, i) => {
    const y = 105 + i * 76;
    circle(70, y, 18, color("--surface"), color("--blue"));
    text(`UE ${i + 1}`, 110, y + 5, 15, color("--ink"));
    ctx.fillStyle = color("--grid");
    ctx.fillRect(175, y - 11, 180, 22);
    ctx.fillStyle = color("--chart");
    ctx.fillRect(175, y - 11, (queue / maxQueue) * 180, 22);
    text(`${queue}/${frame.queue_limit_packets}`, 369, y + 5, 14);
    line(415, y, 465, 245, color("--line"), 2);
  });
  circle(485, 245, 32, color("--accent-soft"), color("--accent"));
  text("gNB", 485, 250, 15, color("--accent"), "center");
  text(
    `${frame.allocated_prbs} PRBs · ${frame.bandwidth_mhz} MHz`,
    485,
    304,
    13,
    color("--muted"),
    "center",
  );
  const chartX = 590,
    chartY = 115,
    chartW = 430,
    chartH = 235,
    maximum = Math.max(frame.capacity_mbps || 1, ...trace.map((item) => item.throughput_mbps || 0));
  text("Observed throughput", chartX, 82, 17, color("--ink"));
  line(chartX, chartY, chartX, chartY + chartH, color("--line"));
  line(chartX, chartY + chartH, chartX + chartW, chartY + chartH, color("--line"));
  const capacityY = chartY + chartH - (frame.capacity_mbps / maximum) * chartH;
  ctx.save();
  ctx.setLineDash([7, 6]);
  line(chartX, capacityY, chartX + chartW, capacityY, color("--accent"), 2);
  ctx.restore();
  text("Capacity reference", chartX + chartW, capacityY - 10, 13, color("--accent"), "right");
  const values = trace.slice(-180);
  values.forEach((item, i) => {
    if (i > 0)
      line(
        chartX + ((i - 1) / Math.max(1, values.length - 1)) * chartW,
        chartY + chartH - (values[i - 1].throughput_mbps / maximum) * chartH,
        chartX + (i / Math.max(1, values.length - 1)) * chartW,
        chartY + chartH - (item.throughput_mbps / maximum) * chartH,
        color("--chart"),
        3,
      );
  });
  text(`${number(maximum)} Mb/s`, chartX, chartY - 12, 12);
  text("Latest sampled frames →", chartX, chartY + chartH + 30, 13);
  text(`Capacity ${number(frame.capacity_mbps)} Mb/s`, 30, 455, 16);
  text(`Phase: ${words(frame.phase)}`, 1050, 455, 16, color("--ink"), "right");
  canvas.setAttribute(
    "aria-label",
    `Telecom ${words(frame.phase)} at ${number(frame.t_seconds, 2)} seconds, ${frame.queue_packets} queued packets, throughput ${number(frame.throughput_mbps)} megabits per second and ${frame.dropped_packets} drops.`,
  );
}
function drawWorkflow(compact) {
  const isCoding = selected === "coding";
  const stages = isCoding
    ? [
        ["Reproduce bug", ["baseline_test_exposes_real_bug"], "Expected test failure"],
        [
          "Constrain change",
          ["path_escape_action_rejected", "stale_patch_rejected"],
          "Unsafe edits rejected",
        ],
        [
          "Verify the fix",
          ["patched_worktree_passes_contract", "only_approved_source_changed"],
          "Tests pass · approved file",
        ],
        [
          "Preserve & clean",
          ["original_checkout_unchanged", "worktree_cleanup", "temporary_repository_removed"],
          "Original intact · cleaned",
        ],
      ]
    : [
        ["Discover tools", ["stdio_handshake_and_tool_discovery"], "Registered tool observed"],
        ["Read tool data", ["real_tool_roundtrip"], "Legitimate build configuration"],
        [
          "Deny invalid calls",
          [
            "schema_rejects_unregistered_target",
            "schema_rejects_missing_required_argument",
            "unknown_execution_tool_rejected",
          ],
          "Schema and tool allowlist",
        ],
        ["Close session", ["server_process_reaped_after_session"], "Child-process exit observed"],
      ];
  const ink = color("--ink"),
    muted = color("--muted"),
    accent = color("--accent");
  text(
    isCoding ? "ISOLATED CHANGE · EXECUTABLE EVIDENCE" : "TOOL ACCESS · OBSERVED BOUNDARIES",
    28,
    40,
    compact ? 24 : 23,
    ink,
  );
  if (compact) {
    stages.forEach(([label, names, note], i) => {
      const checks = names.map(observedCheck),
        ready = checks.every(Boolean),
        passed = ready && checks.every((c) => c.passed);
      const y = 106 + i * 82,
        stroke = ready ? (passed ? accent : color("--danger")) : color("--line");
      if (i) line(52, y - 60, 52, y - 23, color("--line"), 3);
      circle(52, y, 19, ready ? color("--accent-soft") : color("--surface"), stroke);
      text(ready ? (passed ? "✓" : "×") : String(i + 1), 52, y + 8, 23, stroke, "center");
      text(label, 90, y - 2, 28, ink);
      text(ready ? note : "Awaiting observation", 90, y + 28, 22, muted);
    });
  } else {
    stages.forEach(([label, names, note], i) => {
      const checks = names.map(observedCheck),
        ready = checks.every(Boolean),
        passed = ready && checks.every((c) => c.passed);
      const x = 40 + i * 260,
        stroke = ready ? (passed ? accent : color("--danger")) : color("--line");
      ctx.fillStyle = color("--surface");
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.roundRect(x, 90, 230, 115, 14);
      ctx.fill();
      ctx.stroke();
      text(String(i + 1), x + 20, 119, 20, stroke);
      text(label, x + 20, 157, 22, ink);
      text(ready ? (passed ? "Observed" : "Needs attention") : "Awaiting", x + 20, 190, 18, stroke);
      text(note, x + 20, 235, 14, muted);
      if (i < 3) line(x + 232, 160, x + 255, 160, color("--line"), 3);
    });
    if (!isCoding)
      text(
        observedCheck("untrusted_injection_fixture_reaches_boundary")
          ? "Injected text delivered as untrusted data · sanitization is a separate live check"
          : "Tool-result trust is separate from permission to call a tool",
        40,
        290,
        18,
        color("--warning"),
      );
    else
      text("The observed code change appears below after the patch is tested.", 40, 290, 18, muted);
  }
  canvas.setAttribute(
    "aria-label",
    `${definitions[selected].title}: ${stages.map(([label, names]) => label + ": " + (names.every((name) => observedCheck(name)?.passed) ? "observed" : "awaiting or needs attention")).join("; ")}.`,
  );
}
function drawContract() {
  const names =
    selected === "provider"
      ? ["SDK client", "HTTP / SSE", "Local provider"]
      : selected === "tools"
        ? ["MCP client", "JSON-RPC stdio", "Tool subprocess"]
        : ["Task contract", "Git worktree", "Python tests"];
  const xs = [210, 540, 870];
  line(xs[0] + 54, 210, xs[2] - 54, 210, color("--line"), 3);
  names.forEach((name, i) => {
    circle(xs[i], 210, 50, color("--surface"), color("--accent"));
    text(String(i + 1), xs[i], 220, 25, color("--accent"), "center");
    text(name, xs[i], 296, 18, color("--ink"), "center");
  });
  text(`${frame.completed_checks || 0} checks observed`, 540, 375, 24, color("--ink"), "center");
  text(
    words(frame.phase),
    540,
    414,
    16,
    frame.passed ? color("--accent") : color("--danger"),
    "center",
  );
  canvas.setAttribute(
    "aria-label",
    `${definitions[selected].title}, ${frame.completed_checks || 0} checks observed. Latest: ${words(frame.phase)}, ${frame.passed ? "passed" : "failed"}.`,
  );
}
const events = new EventSource("/api/events");
events.addEventListener("open", () => {
  $("connection").textContent = "Connected";
});
events.addEventListener("error", () => {
  $("connection").textContent = "Reconnecting…";
});
events.addEventListener("snapshot", (event) =>
  renderState(JSON.parse(event.data), { revision: Number(event.lastEventId) }),
);
events.addEventListener("reset", (event) =>
  renderState(JSON.parse(event.data), { reset: true, revision: Number(event.lastEventId) }),
);
events.addEventListener("state", (event) =>
  renderState(JSON.parse(event.data), { revision: Number(event.lastEventId) }),
);
events.addEventListener("frame", (event) => {
  const message = JSON.parse(event.data);
  const revision = Number(event.lastEventId);
  if (
    message.run_id !== state.run_id ||
    !Number.isSafeInteger(revision) ||
    revision <= lastRevision ||
    !["running", "cancelling"].includes(state.status)
  )
    return;
  lastRevision = revision;
  state.frames_received = (state.frames_received || 0) + 1;
  $("frame-count").textContent = `${state.frames_received} frames received`;
  applyFrame(message.frame);
});
setInterval(() => {
  $("elapsed").textContent =
    state.status === "running" && state.started_unix
      ? `${number(Date.now() / 1000 - state.started_unix, 1)} s wall`
      : Number.isFinite(state.wall_ms)
        ? `${number(state.wall_ms / 1000, 2)} s wall`
        : "—";
}, 250);
window.addEventListener("resize", requestDraw);
applyTheme();
selectScenario(selected);
