# Third-party tools and attribution

Dependencies retain their upstream licenses. Most are downloaded from their original distribution channels; the official EigenScope SDKs are preserved byte-for-byte in a [verified snapshot](docs/sdk-snapshots.md). The lockfiles identify exact artifacts and hashes. Engine versions appear in results.

| Tool | Role | Upstream |
|---|---|---|
| MuJoCo | Actual dynamics, range sensing and contact simulation | [google-deepmind/mujoco](https://github.com/google-deepmind/mujoco), Apache-2.0 |
| NumPy | Numeric state and vector operations | [numpy/numpy](https://github.com/numpy/numpy), BSD-3-Clause |
| SimPy | Actual discrete-event scheduling and queues | [simpy](https://gitlab.com/team-simpy/simpy), MIT |
| CoolProp | Methane thermophysical properties and equation of state | [CoolProp](https://github.com/CoolProp/CoolProp), MIT |
| SciPy | Transient mass-balance integration and event location | [SciPy](https://github.com/scipy/scipy), BSD-3-Clause |
| asyncua | Actual OPC UA server/client and authorization checks | [FreeOpcUa/opcua-asyncio](https://github.com/FreeOpcUa/opcua-asyncio), LGPL-3.0-or-later |
| OpenAI Python SDK | Real HTTP/SSE client against the local provider fixture | [openai/openai-python](https://github.com/openai/openai-python), Apache-2.0 |
| MCP Python SDK | Actual stdio protocol client/server | [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk), MIT |
| EigenScope SDKs | Actual client-side contracts and authorized hosted API calls | [EigenScope](https://eigenscope.guardianai.network/), package license metadata |
| Open5GS / UERANSIM | Optional real 5G core and simulated RAN/UE topology | [Open5GS](https://github.com/open5gs/open5gs), [UERANSIM](https://github.com/aligungr/UERANSIM); see upstream licenses and [stack attribution](infra/telecom/) |
| docker_open5gs | Optional upstream deployment configuration | [herlesupreeth/docker_open5gs](https://github.com/herlesupreeth/docker_open5gs); pinned checkout and upstream license retained by launcher |
| MongoDB | Optional upstream core subscriber database | [MongoDB licensing](https://www.mongodb.com/legal/licensing/server-side-public-license), SSPL source-available dependency; independently licensed and not represented as OSI open source |

The small warehouse robot model is original lab content. Its ROS2/Nav2-shaped interface is a documented adapter boundary; no ROS2 implementation is vendored. Generated software bills of materials and dependency-audit results should accompany release maintenance.
