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
      "Observe a real failing contract, an isolated patch, passing tests and worktree cleanup.",
    scope: "Actual Git operations and bounded Python test commands",
  },
};
let selected = "robotics";
let state = { status: "idle", frames_received: 0, run_id: 0 };
let lastRun = 0;
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
function updateMetrics() {
  if (!frame) {
    metrics([
      ["Simulation clock", "—", "Begins with the engine"],
      ["Frames", "0", "Actual callback samples"],
      ["Contract checks", "—", "Awaiting a run"],
      ["Seed", $("seed").value, "Reproducible input"],
    ]);
  } else if (selected === "robotics") {
    metrics([
      ["Simulation clock", `${number(frame.t, 2)} s`, "Current phase"],
      ["Speed", `${number(frame.speed_mps, 2)} m/s`, "Actual body velocity"],
      ["Forward range", `${number(frame.range_m, 2)} m`, "MuJoCo ray sensor"],
      [
        "Safety state",
        frame.safety_stop ? "Stopped" : "Moving",
        words(frame.stop_reason || "NONE"),
      ],
    ]);
  } else if (selected === "telecom") {
    metrics([
      ["Simulation clock", `${number(frame.t_seconds, 2)} s`, words(frame.phase)],
      ["Queued packets", String(frame.queue_packets ?? "—"), "All simulated UEs"],
      ["Throughput", `${number(frame.throughput_mbps)} Mb/s`, "Measured over last 10 ms"],
      ["Dropped packets", String(frame.dropped_packets ?? "—"), "Finite-queue drops"],
    ]);
  } else if (selected === "provider" && providerLatest) {
    metrics([
      ["SSE chunks received", String(providerLatest.chunk_index), "Actual parsed HTTP events"],
      ["Content received", `${providerLatest.total_bytes} bytes`, "UTF-8 response content"],
      [
        "Observed stream span",
        `${number(providerLatest.elapsed_ms)} ms`,
        "Wall clock · visual pacing included",
      ],
      [
        "Stream state",
        providerLatest.stream_finished ? "Finished" : "Receiving",
        providerLatest.stream_finished ? "Finish event received" : "Awaiting finish event",
      ],
    ]);
  } else {
    metrics([
      ["Completed checks", String(frame.completed_checks ?? "—"), "Real transport / process work"],
      ["Latest check", frame.passed ? "Passed" : "Failed", words(frame.phase)],
      ["Frames", String(state.frames_received || trace.length), "Actual callback samples"],
      ["Seed", String(state.seed ?? 7), "Deterministic fixtures"],
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
  document
    .querySelectorAll("[data-scenario]")
    .forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.scenario === name)),
    );
  $("scenario-menu").classList.remove("open");
  $("menu-toggle").setAttribute("aria-expanded", "false");
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
  $("phase").textContent = words(frame.phase || "Engine running");
  $("telemetry").textContent = JSON.stringify(frame, null, 2);
  updateMetrics();
  requestDraw();
}
function renderState(next) {
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
  $("reset").disabled = busy;
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
    completed: "Completed",
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
  if (state.status === "idle") {
    frame = null;
    trace = [];
    resetProvider();
    $("phase").textContent = "Choose Run to begin.";
    $("telemetry").textContent = "No frame received.";
  }
  const checks = state.result?.checks || [];
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
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "This action could not complete. Try again.");
    renderState(data);
  } catch (error) {
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
  if (canvas.width !== desiredWidth) {
    canvas.width = desiredWidth;
    canvas.height = compact ? 440 : 500;
  }
  canvas.classList.toggle("compact", compact);
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
  if (compact) {
    drawCompact();
    return;
  }
  if (selected === "robotics") drawRobot();
  else if (selected === "telecom") drawTelecom();
  else if (selected === "provider" && providerLatest) drawProvider(false);
  else drawContract();
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
      frame.safety_stop ? `Safety stop: ${words(frame.stop_reason)}` : "Controller active",
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
      maximum = Math.max(1, ...queues);
    queues.forEach((value, i) => {
      const y = 94 + i * 58;
      text(`UE ${i + 1}`, 28, y + 6, 24, ink);
      ctx.fillStyle = color("--grid");
      ctx.fillRect(130, y - 14, 365, 24);
      ctx.fillStyle = color("--chart");
      ctx.fillRect(130, y - 14, (value / maximum) * 365, 24);
      text(String(value), 525, y + 6, 24, ink);
    });
    text(`${number(frame.throughput_mbps)} Mb/s observed`, 28, 350, 27, ink);
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
  text(`Contacts ${frame.contacts ?? 0}`, 30, 455, 16);
  text(
    frame.safety_stop ? `Safety stop · ${words(frame.stop_reason)}` : "Controller active",
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
    maxQueue = Math.max(1, ...queues);
  queues.forEach((queue, i) => {
    const y = 105 + i * 76;
    circle(70, y, 18, color("--surface"), color("--blue"));
    text(`UE ${i + 1}`, 110, y + 5, 15, color("--ink"));
    ctx.fillStyle = color("--grid");
    ctx.fillRect(175, y - 11, 180, 22);
    ctx.fillStyle = color("--chart");
    ctx.fillRect(175, y - 11, (queue / maxQueue) * 180, 22);
    text(String(queue), 369, y + 5, 14);
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
events.addEventListener("snapshot", (event) => renderState(JSON.parse(event.data)));
events.addEventListener("state", (event) => renderState(JSON.parse(event.data)));
events.addEventListener("frame", (event) => {
  const message = JSON.parse(event.data);
  if (message.run_id !== state.run_id) return;
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
