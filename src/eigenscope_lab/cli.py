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
    models = sub.add_parser(
        "models", help="Test automatic model/profile setup and actual inference latency"
    )
    model_source = models.add_mutually_exclusive_group(required=True)
    model_source.add_argument(
        "--matrix", help="Versioned JSON matrix of local or pinned checkpoints"
    )
    model_source.add_argument(
        "--engine-smoke",
        action="store_true",
        help="Run local GPT-2/Llama inference machinery without SDK calls",
    )
    models.add_argument("--connector", help="Installed SDK adapter in package.module:function form")
    models.add_argument("--allow-downloads", action="store_true")
    models.add_argument("--device", default="cpu")
    models.add_argument("--repeat", type=int, default=3)
    models.add_argument("--warmup", type=int, default=1)
    models.add_argument("--seed", type=int, default=7)
    models.add_argument("--output")
    models.add_argument("--max-projection-p95-ms", type=float)
    models.add_argument("--max-wall-overhead-pct", type=float)
    models.add_argument("--max-ttft-overhead-pct", type=float)
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
            for name in (
                "mujoco",
                "simpy",
                "openai",
                "mcp",
                "eigenscope_sdk",
                "CoolProp",
                "scipy",
                "asyncua",
            )
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
    if args.command == "models":
        from .model_integration import (
            load_connector,
            load_matrix,
            run_engine_smoke,
            run_model_matrix,
        )

        budgets = {
            name: getattr(args, name)
            for name in ("max_projection_p95_ms", "max_wall_overhead_pct", "max_ttft_overhead_pct")
            if getattr(args, name) is not None
        }
        if args.engine_smoke and (args.connector or budgets or args.allow_downloads):
            parser.error(
                "engine smoke uses local models without a connector or integration budgets"
            )
        try:
            if args.engine_smoke:
                result = run_engine_smoke(
                    repeat=args.repeat, warmup=args.warmup, seed=args.seed, device=args.device
                )
            else:
                connector = load_connector(args.connector) if args.connector else None
                result = run_model_matrix(
                    load_matrix(args.matrix),
                    connector=connector,
                    repeat=args.repeat,
                    warmup=args.warmup,
                    seed=args.seed,
                    device=args.device,
                    allow_downloads=args.allow_downloads,
                    budgets=budgets,
                )
        except Exception:
            parser.exit(
                2,
                "Model tests could not start. Check the matrix, connector and inference "
                "environment; see docs/model-integration.md.\n",
            )
        return _emit(result, args.output)
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
