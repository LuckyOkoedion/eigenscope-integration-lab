import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(
  new URL("../../src/eigenscope_lab/assets/web/app.js", import.meta.url),
  "utf8",
);

// Execute the real dashboard consumer. Only browser rendering primitives are
// stubbed; state transitions, HTTP actions and SSE handlers are unchanged.
function dashboard(fetch, { storage, darkPreference = false, drawingContext = {} } = {}) {
  const elements = new Map();
  function element() {
    const classes = new Set();
    return {
      dataset: {},
      listeners: new Map(),
      children: [],
      disabled: false,
      hidden: false,
      value: "7",
      textContent: "",
      classList: {
        contains: (name) => classes.has(name),
        remove: (name) => classes.delete(name),
        toggle(name) {
          if (classes.has(name)) return (classes.delete(name), false);
          classes.add(name);
          return true;
        },
      },
      setAttribute(name, value) {
        this[name] = value;
      },
      addEventListener(name, listener) {
        this.listeners.set(name, listener);
      },
      replaceChildren(...children) {
        this.children = children;
      },
      append(...children) {
        this.children.push(...children);
      },
      focus() {},
      getContext: () => drawingContext,
    };
  }
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  let stream;
  const context = vm.createContext({
    document: {
      getElementById: get,
      querySelectorAll: () => [],
      createElement: element,
      documentElement: element(),
      addEventListener() {},
    },
    window: { addEventListener() {} },
    localStorage: storage || { getItem: () => "light", setItem() {} },
    matchMedia: () => ({ matches: darkPreference }),
    getComputedStyle: () => ({ getPropertyValue: () => "#142239" }),
    requestAnimationFrame() {},
    setInterval() {},
    fetch,
    EventSource: class {
      constructor() {
        stream = this;
        this.handlers = new Map();
      }
      addEventListener(name, handler) {
        this.handlers.set(name, handler);
      }
    },
  });
  vm.runInContext(source, context);
  return {
    get,
    read: (expression) => vm.runInContext(expression, context),
    click: (id) => get(id).listeners.get("click")(),
    emit: (kind, revision, data) =>
      stream.handlers.get(kind)({ lastEventId: String(revision), data: JSON.stringify(data) }),
  };
}

const snapshot = (status, event_id, run_id = 2) => ({
  status,
  event_id,
  run_id,
  scenario: "oil-gas",
  seed: 7,
  frame: null,
  frames_received: 0,
  result: null,
  history: [],
});

function deferredResponse() {
  let resolve;
  const body = new Promise((done) => (resolve = done));
  return { fetch: async () => ({ ok: true, json: () => body }), resolve };
}

test("a deferred cancel response cannot undo the newer terminal SSE state", async () => {
  const response = deferredResponse();
  const app = dashboard(response.fetch);
  app.emit("snapshot", 10, snapshot("running", 10));
  const stopping = app.click("cancel");
  app.emit("state", 11, snapshot("cancelling", 11));
  app.emit("state", 12, snapshot("cancelled", 12));
  response.resolve(snapshot("cancelling", 11));
  await stopping;
  assert.equal(app.read("state.status"), "cancelled");
  assert.equal(app.get("run-status").textContent, "Stopped");
  assert.equal(app.get("reset").disabled, false);
  assert.equal(app.get("start").disabled, false);
  assert.equal(app.get("cancel").disabled, true);
});

test("equal revisions favor terminal state and reject duplicate or stale frames", () => {
  const app = dashboard();
  app.emit("snapshot", 10, snapshot("running", 10));
  const frame = { run_id: 2, frame: { phase: "stable-production", pressure_pa: 1_500_000 } };
  app.emit("frame", 11, frame);
  app.emit("frame", 11, frame);
  assert.equal(app.read("state.frames_received"), 1);
  app.emit("state", 11, snapshot("cancelled", 11));
  app.emit("state", 11, snapshot("cancelling", 11));
  app.emit("frame", 12, frame);
  assert.equal(app.get("run-status").textContent, "Stopped");
  assert.equal(app.get("reset").disabled, false);
  assert.equal(app.read("lastRevision"), 11);
});

test("explicit reconnect reset accepts a new server counter and discards old HTTP response", async () => {
  const response = deferredResponse();
  const app = dashboard(response.fetch);
  app.emit("snapshot", 100, snapshot("running", 100));
  const stopping = app.click("cancel");
  app.emit("reset", 0, snapshot("idle", 0, 0));
  response.resolve(snapshot("cancelling", 101));
  await stopping;
  assert.equal(app.read("state.status"), "idle");
  assert.equal(app.read("lastRevision"), 0);
  app.emit("state", 1, snapshot("running", 1, 1));
  assert.equal(app.read("state.run_id"), 1);
  assert.equal(app.get("run-status").textContent, "Running");
});

test("SSE envelope revision takes precedence over a stale embedded revision", () => {
  const app = dashboard();
  app.emit("snapshot", 10, snapshot("running", 10));
  app.emit("state", 12, snapshot("cancelled", 11));
  app.emit("state", 11, snapshot("cancelling", 11));
  assert.equal(app.read("lastRevision"), 12);
  assert.equal(app.get("run-status").textContent, "Stopped");
});

test("multimodal terminal video frame displays OCVC modality and frame dimensions", () => {
  const app = dashboard();
  app.emit("snapshot", 1, {
    ...snapshot("completed", 1, 1),
    scenario: "multimodal",
    frame: {
      phase: "tamper-and-ceiling-controls",
      modality: "video_clip",
      container_bytes: 147472,
      video_frame_count: 6,
      width: 96,
      height: 64,
      rejected_controls: 6,
      expected_rejected_controls: 6,
      exact_roundtrip: true,
      active_stage: "VERIFIED",
    },
  });
  const cards = app.get("metrics").children;
  assert.equal(cards[0].children[1].textContent, "Video (OCVC)");
  assert.equal(cards[1].children[2].textContent, "6 frames · 96×64 RGBA");
});

test("live mode indicator and check display formatting", () => {
  const app = dashboard();
  app.emit("snapshot", 1, {
    ...snapshot("failed", 1, 1),
    live_mode: true,
    service_origin: "https://service.example.invalid",
    scenario: "multimodal",
    result: {
      checks: [
        {
          name: "consumed_ticket_reuse_denied",
          passed: true,
          http_status: 410,
          error_code: "VAULT_TICKET_SCRUBBED",
        },
        {
          name: "live_v1_audio_actual_multimodal_decomposition_succeeded",
          passed: false,
          http_status: 503,
          error_code: "CORE_AUTHORITY_UNAVAILABLE",
          reason_code: "SURFACE_DENIED_UNTIL_QUALIFIED",
        },
      ],
    },
  });
  assert.equal(app.get("mode-label").textContent, "Live EigenScope API");
  const items = app.get("checks").children;
  assert.equal(items[0].children[1].textContent, "Single-use vault ticket replay blocked");
  assert.equal(
    items[1].children[1].textContent,
    "Live audio separation — temporarily unavailable. Try again.",
  );
});

test("failed checks identify the observed failure without inventing its cause", () => {
  const app = dashboard();
  const display = (check) => app.read(`formatCheckDisplay(${JSON.stringify(check)})`);
  assert.equal(
    display({ name: "rag_clean_ground_truth_ranked_first", passed: false, http_status: 200 }),
    "Live ground-truth ranking — needs attention. Try again.",
  );
  assert.equal(
    display({ name: "workflow_completed", passed: false, http_status: 422 }),
    "Live service request completed — needs attention. Try again.",
  );
  assert.equal(
    display({ name: "workflow_completed", passed: false, error_code: "INVALID_RESPONSE_SCHEMA" }),
    "Live service request completed — response format needs attention. Try again.",
  );
  assert.equal(
    display({ name: "workflow_completed", passed: false, http_status: 503 }),
    "Live service request completed — temporarily unavailable. Try again.",
  );
  assert.equal(
    display({
      name: "workflow_completed",
      passed: false,
      http_status: 400,
      error_code: "CORE_AUTHORITY_UNAVAILABLE",
    }),
    "Live service request completed — needs attention. Try again.",
  );
});

test("blocked browser storage does not prevent initialization, theme switching or SSE updates", () => {
  const app = dashboard(undefined, {
    darkPreference: true,
    storage: {
      getItem() {
        throw new Error("Storage blocked");
      },
      setItem() {
        throw new Error("Storage blocked");
      },
    },
  });
  assert.equal(app.read("document.documentElement.dataset.theme"), "dark");
  app.click("theme");
  assert.equal(app.read("document.documentElement.dataset.theme"), "light");
  app.emit("snapshot", 1, snapshot("running", 1));
  assert.equal(app.get("run-status").textContent, "Running");
});

test("an invalid saved theme falls back to the browser preference", () => {
  const app = dashboard(undefined, {
    darkPreference: true,
    storage: { getItem: () => "corrupt-value", setItem() {} },
  });
  assert.equal(app.read("document.documentElement.dataset.theme"), "dark");
});

test("only boolean true can display a passed check or successful workflow observation", () => {
  const app = dashboard();
  app.emit("snapshot", 1, {
    ...snapshot("failed", 1, 1),
    result: {
      checks: ["false", 1, null, false, true].map((passed, i) => ({
        name: `check_${i}`,
        passed,
      })),
    },
  });
  assert.equal(app.get("check-count").textContent, "1 / 5 passed");
  assert.equal(app.get("checks").children[0].children[0].textContent, "×");
  assert.equal(app.get("checks").children[0].children[0].className, "check-mark failed");
  assert.equal(app.get("checks").children[4].children[0].textContent, "✓");
  assert.equal(app.read('observedOutcome("check_0")'), "Needs attention");
});

test("selecting a different scenario clears activity from the previous run and preserves acronyms", () => {
  const app = dashboard();
  app.emit("snapshot", 1, {
    ...snapshot("completed", 1, 1),
    scenario: "robotics",
    result: { checks: [{ name: "robotics_contract", passed: true }] },
  });
  assert.equal(app.get("activity").children[0].textContent, "Run 1 passed.");
  app.read('selectScenario("rag")');
  assert.equal(app.get("activity").children.length, 1);
  assert.equal(app.get("activity").children[0].textContent, "RAG defense is ready to run.");
  assert.equal(app.get("start").textContent, "Run RAG defense");
  app.read('selectScenario("tools")');
  assert.equal(app.get("start").textContent, "Run MCP tools");
});

test("resetting a finished view clears its activity and any old scenario error", () => {
  const app = dashboard();
  app.emit("snapshot", 1, { ...snapshot("failed", 1, 1), error: "Earlier run failed" });
  app.emit("state", 2, snapshot("idle", 2, 1));
  assert.equal(app.get("activity").children.length, 1);
  assert.equal(app.get("activity").children[0].textContent, "Oil & Gas is ready to run.");
  assert.equal(app.get("feedback").hidden, true);
});

test("retriever comparison displays actual rows and keeps unobserved metrics unavailable", () => {
  const app = dashboard();
  assert.equal(app.get("rag-benchmark").hidden, true);
  app.emit("snapshot", 1, {
    ...snapshot("completed", 1, 1),
    scenario: "rag",
    result: {
      checks: [{ name: "ranking", passed: true }],
      metrics: {
        benchmark_rows: [
          {
            retriever: "legacy_bm25",
            display_name: "BM25 lexical",
            clean_ndcg_at_k: 0.7619,
            poison_target_exposure_pct: 40,
          },
          {
            retriever: "legacy_dense_bi_encoder",
            clean_ndcg_at_k: 0,
            poison_target_exposure_pct: 0,
          },
          { retriever: "unavailable" },
        ],
      },
    },
  });
  assert.equal(app.get("rag-benchmark").hidden, false);
  const rows = app.get("benchmark-rows").children;
  assert.deepEqual(
    rows[0].children.map((cell) => cell.textContent),
    ["BM25 lexical", "0.7619", "40.0%"],
  );
  assert.deepEqual(
    rows[1].children.map((cell) => cell.textContent),
    ["Dense LSA", "0.0000", "0.0%"],
  );
  assert.deepEqual(
    rows[2].children.map((cell) => cell.textContent),
    ["unavailable", "—", "—"],
  );
  app.read('selectScenario("robotics")');
  assert.equal(app.get("rag-benchmark").hidden, true);
  assert.equal(app.get("benchmark-rows").children.length, 0);
});

test("the RAG diagram never invents a retrieved document, verified rank or perfect nDCG", () => {
  const drawn = [];
  const drawingContext = new Proxy(
    {
      fillText: (value) => drawn.push(value),
      measureText: (value) => ({ width: value.length * 9 }),
    },
    { get: (target, property) => target[property] || (() => {}) },
  );
  const app = dashboard(undefined, { drawingContext });
  app.emit("snapshot", 1, {
    ...snapshot("running", 1, 1),
    scenario: "rag",
    frame: {
      phase: "gated-retrieval",
      admitted_context: [],
      searchable_count: 4,
      quarantined_count: 0,
    },
  });
  app.read("canvas.width = 640; drawRag(true)");
  assert.ok(drawn.includes("Awaiting top passage"));
  assert.ok(drawn.includes("Pending retrieval"));
  assert.equal(
    drawn.some(
      (value) => value.includes("kb_invoice_proration") || value.includes("Ground-truth #1"),
    ),
    false,
  );
  assert.match(app.get("scene")["aria-label"], /clean nDCG —/);
});

test("upstream error bodies and internal check names never become visible recovery copy", async () => {
  const detail =
    "CORE_AUTHORITY_UNAVAILABLE /srv/runtime/profile.bin Traceback: synthetic diagnostic";
  const app = dashboard(async () => ({ ok: false, json: async () => ({ error: detail }) }));
  app.emit("snapshot", 1, { ...snapshot("failed", 1, 1), error: detail });
  assert.equal(app.get("feedback").textContent, "We couldn’t complete this run. Try again.");
  assert.equal(
    app.read(`formatCheckDisplay(${JSON.stringify({ name: detail, passed: false })})`),
    "Scenario check",
  );
  await app.click("reset");
  assert.equal(app.get("feedback").textContent, "We couldn’t complete this action. Try again.");
});

test("a live service outage never claims a tier requirement or completed restoration", () => {
  const app = dashboard();
  app.emit("snapshot", 1, {
    ...snapshot("failed", 1, 1),
    scenario: "multimodal",
    live_mode: true,
    frame: {
      phase: "live-sdk-audio_wav",
      modality: "audio_wav",
      live_failed: true,
      live_endpoint_unavailable: true,
      exact_roundtrip: false,
      live_modality_status: { audio_wav: "UNAVAILABLE" },
    },
  });
  const restoration = app.get("metrics").children[2];
  assert.equal(restoration.children[1].textContent, "Not completed");
  assert.equal(restoration.children[2].textContent, "Service unavailable. Try again.");
});

test("failed tool filtering never claims identifier protection or invents a policy fix", () => {
  const app = dashboard();
  app.emit("snapshot", 1, {
    ...snapshot("failed", 1, 1),
    scenario: "tools",
    frame: {
      phase: "live-protection-checks",
      observed_checks: [
        { name: "private_values_absent_from_outbound_core", passed: false },
        { name: "untrusted_directives_absent", passed: false },
      ],
    },
  });
  const filtering = app.get("metrics").children[2];
  assert.equal(filtering.children[1].textContent, "Needs attention");
  assert.equal(filtering.children[2].textContent, "Review the highlighted checks");
  assert.equal(
    app.read(
      'formatCheckDisplay({name: "rag_ingest_succeeded_and_receipt_fields_consistent", passed: true})',
    ),
    "Live corpus ingestion and receipt checks",
  );
});

for (const [status, expected] of [
  ["failed", "Needs attention"],
  ["cancelled", "Stopped"],
]) {
  test(`${status} before the first frame does not look like an unstarted run`, () => {
    const app = dashboard();
    app.emit("snapshot", 1, snapshot(status, 1, 1));
    assert.equal(app.get("metrics").children[0].children[1].textContent, expected);
    assert.equal(app.get("check-count").textContent, "No completed checks");
    assert.equal(app.get("start").textContent, "Run again");
    assert.doesNotMatch(app.get("check-empty").textContent, /^Run this scenario/);
  });
}
