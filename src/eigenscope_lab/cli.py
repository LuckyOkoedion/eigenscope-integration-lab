"""Command line entry point. Live calls require an explicitly selected subcommand."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from typing import Any

from .reporting import report, write_report
from .runner import SCENARIOS, run_scenario


def _emit(result: dict[str, Any], output: str | None) -> int:
    if output:
        write_report(result, output)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    statuses = result.get("summary", {})
    if statuses.get("ERROR", 0):
        return 3
    if statuses.get("FAIL", 0):
        return 1
    if statuses.get("BLOCKED", 0):
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run reproducible industry integration and SDK contract scenarios."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List available local industry scenarios")
    sub.add_parser("doctor", help="Check prerequisites without reading or printing credentials")
    visual = sub.add_parser(
        "serve", help="Open a loopback dashboard streaming actual simulator telemetry"
    )
    visual.add_argument(
        "--host", choices=["127.0.0.1", "localhost", "0.0.0.0"], default="127.0.0.1"
    )
    visual.add_argument("--port", type=int, default=8765)
    visual.add_argument(
        "--container",
        action="store_true",
        help="Explicitly permit container interface binding; publish the port on host loopback only",
    )
    local = sub.add_parser(
        "run", help="Execute local external-system scenarios; no EigenScope calls"
    )
    local.add_argument("scenario", choices=["all", *SCENARIOS])
    local.add_argument("--seed", type=int, default=7)
    local.add_argument("--output")
    live = sub.add_parser(
        "live",
        help="Run synthetic cases through the real EigenScope SDK/API and scrub their own tickets",
    )
    live.add_argument("--output")
    live.add_argument("--seed", type=int, default=7)
    live.add_argument("--repeat", type=int, default=1, choices=range(1, 11), metavar="1..10")
    live.add_argument("--case", default="all", help="Case ID or all; see docs/live-testing.md")
    live.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    if hasattr(args, "seed") and not 0 <= args.seed <= 4294967295:
        parser.error("seed must be between 0 and 4294967295")
    if args.command == "serve":
        if not 0 <= args.port <= 65535:
            parser.error("port must be between 0 and 65535")
        if args.host == "0.0.0.0" and not args.container:
            parser.error("all-interface binding requires explicit --container")
        from .web import serve

        try:
            serve(host=args.host, port=args.port, allow_container=args.container)
        except KeyboardInterrupt:
            return 0
        return 0
    if args.command == "list":
        for name in SCENARIOS:
            print(name)
        return 0
    if args.command == "doctor":
        packages = {
            name: importlib.util.find_spec(name) is not None
            for name in ("mujoco", "simpy", "openai", "mcp", "eigenscope_sdk")
        }
        print(
            json.dumps(
                {
                    "python_supported": sys.version_info >= (3, 11),
                    "packages": packages,
                    "git": shutil.which("git") is not None,
                    "docker_cli": shutil.which("docker") is not None,
                    "api_key_configured": bool(os.environ.get("ORTHOCORE_API_KEY")),
                    "scope": "No network calls; Docker CLI presence does not prove daemon availability.",
                },
                indent=2,
            )
        )
        return 0 if all(packages.values()) and shutil.which("git") else 2
    if args.command == "run":
        names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
        return _emit(
            report(
                [run_scenario(name, args.seed) for name in names],
                "local-external-simulators",
                args.seed,
            ),
            args.output,
        )
    try:
        from .live import run_live

        result = run_live(
            seed=args.seed, repeat=args.repeat, case_id=args.case, timeout=args.timeout
        )
    except (ValueError, RuntimeError):
        parser.exit(
            2,
            "Live tests could not start. Verify ORTHOCORE_BASE_URL, ORTHOCORE_API_KEY and installed extras; see docs/live-testing.md.\n",
        )
    return _emit(result, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
