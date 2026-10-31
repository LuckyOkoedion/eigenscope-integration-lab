# Optional real 5GC/UE protocol stack

`stack.py` prepares an isolated Open5GS/UERANSIM deployment from the immutable
configuration and image references in `upstream.lock.json`. Start with the
[telecom scenario guide](../../docs/scenarios/telecom.md).

The normal lab runs a fast SimPy scheduler. This optional tier tests actual
5GC registration and tunneled packets, using Linux/amd64 containers. It does not
silently substitute a queue model when native prerequisites are missing.

## Lifecycle

Run `doctor`, `prepare`, `pull`, `up`, `verify`, then `down` using the commands in
the scenario guide. `verify` exits nonzero unless all declared registration,
tunnel and user-plane checks pass. The generated Compose file, source checkout
and verification JSON live under ignored `.lab/telecom/`.

The launcher changes no host networking settings and publishes no ports. Its
internal network is `10.231.0.0/24`; the synthetic UE subnets are `10.251.0.0/24`
and `10.252.0.0/24`. An overlapping existing Docker network blocks startup.
The UPF and UE containers require the upstream TUN/NET_ADMIN privileges inside
the Linux Docker VM. They are never connected to radio hardware.

`down` targets only the lab's Compose project and its own volume. It does not
prune Docker, remove other workloads, stop Colima or change the selected context.

## Provenance and upgrades

The configuration checkout is pinned to a full Git commit. Executable images are
pinned to immutable Linux/amd64 manifests. Their native versions are collected
by `verify`; no source/image identity is inferred from sharing an upstream name.
The lock file lists the independently licensed dependencies. The upstream
checkout retains all copyright and license notices.

The optional, not-yet-validated `build-ueransim` step builds current UERANSIM 3.3.0 from commit
`6bf5a1a96aaef6ae8778b9d8b477ac6e2bbf8156`, retaining its AGPL license inside
the image. A local immutable image ID is recorded and selected automatically.
The separately pinned upstream prebuilt baseline contains UERANSIM 3.2.6; it is
not described as the current source release. The source recipe pins its base and
source revision while apt supplies current dependencies during the build.
The source-build option is experimental; the validated baseline uses the
pinned prebuilt image.

Review upgrades as a change: resolve new immutable image digests, review upstream
changes, run registration/tunnel checks, then commit the new lock and sanitized
verification evidence. Do not replace the lock with `latest` or `master` tags.

## Troubleshooting

- **Docker unavailable:** select the correct existing daemon with `--context`.
  On macOS with Colima, use `--context colima`.
- **Unsupported architecture:** the pinned images are Linux/amd64. Use an
  amd64 Linux VM or the portable SimPy tier.
- **SCTP/TUN failure:** the Linux Docker host must support these kernel features.
  The launcher does not load host modules or change host device configuration.
- **Registration does not complete:** inspect only this synthetic stack with
  `docker --context colima compose -p eigenscope-lab-5gc -f .lab/telecom/compose.json logs --tail 100 amf nr_gnb nr_ue`.
- **No user-plane ping:** inspect the synthetic tunnel and UPF state. This check
  uses a local UPF destination; internet access is deliberately unnecessary.
- **Insufficient memory/disk:** increase resources on a dedicated test VM. The
  launcher does not restart a VM that may host unrelated workloads.

Native protocol validation status is recorded separately from the portable
scenario results. An image pull or successful container start alone is not a
registration/user-plane pass.

## Recorded validation

**PASS:** the [final clean Linux CI run](https://github.com/LuckyOkoedion/eigenscope-integration-lab/actions/workflows/native-5g.yml)
passed all five native checks: successful UE status command,
registered UE, assigned tunnel address, tunneled user-plane ping and zero packet
loss. The lab-only cleanup also passed.

Actual versions were Open5GS `2.8.0-48-gf87da61`, UERANSIM `3.2.6` and MongoDB
`7.0.45`. The synthetic UE received `10.251.0.2`; all three ICMP packets returned,
with observed mean RTT `0.727 ms`. This measurement covers those three synthetic
UE-to-UPF packets through the actual GTP-U tunnel.

[Sanitized native evidence](../../docs/verification/native-5g.json) contains the
checks, immutable image references, runtime versions, environment and cleanup
result. The result includes the exact executable versions and image references.

The database image is pinned to MongoDB 7.0.45. Review the upstream
[kernel compatibility notes](https://www.mongodb.com/docs/manual/release-notes/8.0/)
when changing major version. Use a fresh lab volume for database-major upgrades;
`down` removes only this lab's named volume.
