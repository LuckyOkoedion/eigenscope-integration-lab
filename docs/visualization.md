# Watch a scenario run

Start the local dashboard:

```bash
uv sync --frozen --all-extras
uv run eigenscope-lab serve --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` in a browser. Select Robotics, Telecom, Oil & Gas, Model provider, MCP tools or Coding workflow, choose a seed, and select **Run**. The dashboard needs no API credentials. Authenticated EigenScope testing remains an explicit CLI operation.

Run guidance explains the next step as a scenario starts, runs, stops or finishes. **Passed** means all returned checks passed. **Run again** repeats the selected scenario; **Reset view** clears a finished run's presentation. On mobile, **Scenarios** displays the current selection. Escape closes the menu and returns keyboard focus to its button.

The visualization follows measurements from the installed engines:

- **Robotics:** MuJoCo body positions, heading, obstacle geometry, ray range, contacts and safety-monitor state. The clear route, obstacle stop, intentionally unguarded physics control and sensor-loss phases have separate simulation clocks.
- **Telecom:** SimPy subscriber queues, observed throughput, finite-capacity allocation and overload drops. This view is a discrete-event model, not a rendering of an E2AP or physical radio connection.
- **Oil & Gas:** Receiver pressure and high-high threshold, inlet/outlet flow, effective compressor load and independent shutdown state. The pressure trace follows each operating phase. An approved load-change phase, rejected proposals, an underreported pressure sensor and stale data exercise separate boundaries.
- **Provider:** Actual SDK-parsed SSE chunks, cumulative UTF-8 response bytes, observed arrival times, finish and usage events, and a bounded preview of the synthetic response. The cancellation note appears only after the local HTTP server observes the upstream disconnect. Subsequent fault-check phases preserve the observed stream timeline.
- **Tools and coding:** Checkpoint events after actual MCP subprocess or Git/test operations. The diagram updates as each operation completes.

**Playback pace** adds a chosen wall-clock delay after a callback so fast simulations can be inspected. **Fastest** removes that added delay. The simulation clock advances independently of this display setting. Wall time, simulation time and frame count are presented separately.

**Stop** requests cancellation at the next simulator checkpoint. Native subprocess work may finish its bounded operation before the next checkpoint; the UI displays **Stopping** until the worker exits. **Reset view** clears the current presentation after a run ends. Recent-run summaries remain available for the server session. Completed runs can be downloaded as JSON.

The interface supports light and dark themes, keyboard controls, a compact scenario menu, responsive diagram layouts and text equivalents for the latest measurements. Charts redraw only when real telemetry arrives. There are no remote fonts, CDNs or decorative motion dependencies.

The server binds only to loopback by default. The Docker distribution explicitly enables an internal `0.0.0.0` bind so Docker can forward the port; its published port must remain `127.0.0.1:8765:8765`. This explicit mode retains the same loopback Host and browser-Origin checks and does not permit arbitrary network addresses. It exposes fixed assets and small JSON actions, admits one active simulation, keeps a maximum of 512 replay events and 20 history summaries, limits concurrent event streams, and rejects unexpected credential fields. It is a local developer tool, not a multi-user hosted service. It neither reads credentials nor provides a live API action from the browser.

Disconnects reconnect through SSE event IDs. A fresh server or an expired replay window sends the current state and begins a new observation window. Detailed local exceptions go to the terminal; the interface provides a clear recovery message.

## Read the domain view

Each view leads with the operational question being tested. Robotics pairs its path with goal distance, configured stop range, sensor age and the monitor state; the collision-control phase explicitly bypasses the monitor for that experiment. Telecom queue bars use the configured finite queue limit, while the throughput plot marks capacity. Its p95 and fairness values describe delivered packets accumulated within the current phase.

The provider view distinguishes first nonempty content receipt, finish receipt and observed upstream cancellation. These are local fixture timings with playback pacing included. MCP shows discovery, permitted data retrieval, denied calls and subprocess cleanup; delivery of an injected tool result is labelled as untrusted input, independently of a live sanitization test. Coding shows the actual observed source change, expected baseline failure, tested fix and original-checkout/cleanup results.

Oil & Gas plots process truth and the published sensor separately against the high-high threshold. Requested and effective compressor load remain distinct after shutdown. The stale-sensor experiment uses a separate initialized receiver and is labelled accordingly.
