# Optional Inductive Automation Ignition 8.1 + Sparkplug B Stack

Run a real, pinned **Inductive Automation Ignition 8.1.48** Gateway alongside an **Eclipse Mosquitto 2.0.20** MQTT / Sparkplug B broker in an isolated local Docker stack before deploying EigenScope's production OT clients.

Start with the portable [Oil & Gas scenario](../../docs/scenarios/oil-gas.md) first (`uv run --frozen eigenscope-lab run oil-gas`), which needs no Docker daemon and exercises CoolProp, SciPy, OPC UA Basic256Sha256 SignAndEncrypt, loopback MQTT v3.1.1 Sparkplug B UNS, and AVEVA PI / Cognite CDF contracts directly in Python. Use this optional stack when you want to point external SCADA tags, Ignition's built-in **Programmable Device Simulator**, or a compiled `.modl` build against a live Gateway UI and broker.

## Requirements & Licensing

- Linux `x86_64` or `arm64` Docker daemon with Compose v2 (`docker --context colima` works on macOS via Colima).
- At least `2 GiB` Docker memory (`4 GiB+` recommended).
- **Explicit EULA acceptance:** Passing `--accept-ignition-eula` confirms your acceptance of Inductive Automation's EULA (`ACCEPT_IGNITION_EULA=Y`). The container starts in **2-hour resettable trial mode** without a commercial license key.
- Multi-architecture image digests are pinned in [upstream.lock.json](upstream.lock.json). All published ports bind strictly to `127.0.0.1` (`8088` Gateway Web UI, `62541` Ignition OPC UA, `1883` Mosquitto MQTT/Sparkplug B).

## Launch, Verify & Connect Ignition's Programmable Simulator

```sh
python infra/ignition/stack.py doctor --context colima
python infra/ignition/stack.py pull --context colima
python infra/ignition/stack.py up --context colima --accept-ignition-eula
python infra/ignition/stack.py verify
```

1. Retrieve the generated admin password stored with mode `0600` at `.lab/ignition/.admin-password` and open `http://127.0.0.1:8088`.
2. In the Ignition Gateway UI, navigate to **Config > OPC UA > Device Connections > Create new Device** and select **Programmable Device Simulator** to generate live simulated OPC UA tags on `127.0.0.1:62541` (update OPC UA **Bind Address** to `0.0.0.0` inside **Config > OPC UA > Server Settings** if connecting from outside the container).
3. Test reading/gating those telemetry frames through the local OT DMZ workflow or load an unsigned dev build of the production `.modl` from [`ignition-scada-uns-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/ignition-scada-uns-kit.zip) by starting with `--allow-unsigned-modules`.

## Tear Down Cleanly

```sh
python infra/ignition/stack.py down --context colima
```

Removes only the isolated `eigenscope-lab-ignition` containers, network (`10.232.0.0/24`), and named volume (`eigenscope-lab-ignition-data`).

## Moving to Production

Once local testing passes, deploy the production OT clients documented under [Oil & Gas](https://eigenscope.guardianai.network/guide/oil-gas) and [Integrations](https://eigenscope.guardianai.network/integrations):

- **Ignition `.modl` + Sparkplug B UNS Filter:** [`ignition-scada-uns-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/ignition-scada-uns-kit.zip)
- **Cognite CDF & AVEVA PI Vision Connector:** [`industrial-historian-cdf-pi-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/industrial-historian-cdf-pi-kit.zip)
- **Purdue L3.5 Air-Gapped OT DMZ Kit:** [`oil-gas-ot-dmz-kit.zip`](https://eigenscope.guardianai.network/downloads/kits/oil-gas-ot-dmz-kit.zip)
