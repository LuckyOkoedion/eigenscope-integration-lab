# Watch a scenario run

Start the local dashboard:

```bash
uv sync --frozen --all-extras
uv run eigenscope-lab serve --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765` in a browser. Select Robotics, Telecom, Model provider, MCP tools or Coding workflow, choose a seed, and select **Run**. The dashboard needs no API credentials. Authenticated EigenScope testing remains an explicit CLI operation.

Run guidance explains the next step as a scenario starts, runs, stops or finishes. **Passed** means all returned checks passed. **Run again** repeats the selected scenario; **Reset view** clears a finished run's presentation. On mobile, **Scenarios** displays the current selection. Escape closes the menu and returns keyboard focus to its button.

The visualization follows measurements from the installed engines:

- **Robotics:** MuJoCo body positions, heading, obstacle geometry, ray range, contacts and safety-monitor state. The clear route, obstacle stop, intentionally unguarded physics control and sensor-loss phases have separate simulation clocks.
- **Telecom:** SimPy subscriber queues, observed throughput, finite-capacity allocation and overload drops. This view is a discrete-event model, not a rendering of an E2AP or physical radio connection.
- **Provider:** Actual SDK-parsed SSE chunks, cumulative UTF-8 response bytes, observed arrival times, finish and usage events, and a bounded preview of the synthetic response. The cancellation note appears only after the local HTTP server observes the upstream disconnect. Subsequent fault-check phases preserve the observed stream timeline.
- **Tools and coding:** Checkpoint events after actual MCP subprocess or Git/test operations. The diagram updates as each operation completes.

**Playback pace** adds a chosen wall-clock delay after a callback so fast simulations can be inspected. **Fastest** removes that added delay. The simulation clock advances independently of this display setting. Wall time, simulation time and frame count are presented separately.

**Stop** requests cancellation at the next simulator checkpoint. Native subprocess work may finish its bounded operation before the next checkpoint; the UI displays **Stopping** until the worker exits. **Reset view** clears the current presentation after a run ends. Recent-run summaries remain available for the server session. Completed runs can be downloaded as JSON.

The interface supports light and dark themes, keyboard controls, a compact scenario menu, responsive diagram layouts and text equivalents for the latest measurements. Charts redraw only when real telemetry arrives. There are no remote fonts, CDNs or decorative motion dependencies.

The server binds only to loopback by default. The Docker distribution explicitly enables an internal `0.0.0.0` bind so Docker can forward the port; its published port must remain `127.0.0.1:8765:8765`. This explicit mode retains the same loopback Host and browser-Origin checks and does not permit arbitrary network addresses. It exposes fixed assets and small JSON actions, admits one active simulation, keeps a maximum of 512 replay events and 20 history summaries, limits concurrent event streams, and rejects unexpected credential fields. It is a local developer tool, not a multi-user hosted service. It neither reads credentials nor provides a live API action from the browser.

Disconnects reconnect through SSE event IDs. A fresh server or an expired replay window sends the current state and begins a new observation window. Detailed local exceptions go to the terminal; the interface provides a clear recovery message.
