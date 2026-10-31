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
function dashboard(fetch) {
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
      getContext: () => ({}),
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
    localStorage: { getItem: () => "light", setItem() {} },
    matchMedia: () => ({ matches: false }),
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
