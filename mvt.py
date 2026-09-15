#!/usr/bin/env python3
"""Thin agent-facing interface for the authoritative Ch4oS lifecycle harness."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SMOKE = ROOT / "smoke.py"


def build_smoke_command(args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable,
        str(SMOKE),
        "--pack-sha", args.pack_sha,
        "--output", args.output,
        "--image", args.image,
        "--heap", args.heap,
        "--container-memory", args.container_memory,
        "--boot-timeout", str(args.boot_timeout),
    ]
    if args.accept_eula:
        command.append("--accept-eula")
    return command


def read_report(output: str) -> dict | None:
    path = Path(output).resolve() / "report.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def fallback_detail(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    if not text:
        return f"smoke.py exited {result.returncode} without a report"
    return text.splitlines()[-1]


def summarize(report: dict | None, result: subprocess.CompletedProcess[str], output: str) -> dict:
    evidence = str(Path(output).resolve())
    if report is None:
        return {
            "status": "fail" if result.returncode == 1 else "error",
            "suite": "lifecycle",
            "detail": fallback_detail(result),
            "evidence": evidence,
        }

    tests = report.get("tests", [])
    passed = sum(item.get("status") == "passed" for item in tests)
    summary = {
        "status": "pass" if report.get("status") == "lifecycle_passed_logs_unreviewed" else report.get("status", "error"),
        "suite": "lifecycle",
        "pack_sha": report.get("pack_sha"),
        "minecraft": report.get("versions", {}).get("minecraft"),
        "neoforge": report.get("versions", {}).get("neoforge"),
        "checks_passed": passed,
        "checks_total": len(tests),
        "evidence": evidence,
    }
    failed = next((item for item in tests if item.get("status") in ("failed", "error")), None)
    if failed is not None:
        summary["status"] = "fail" if failed.get("status") == "failed" else "error"
        summary["failed_step"] = failed.get("name")
        summary["detail"] = failed.get("detail", "")
    return summary


def emit(summary: dict, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(summary, separators=(",", ":")))
        return
    status = summary["status"].upper()
    if summary["status"] == "pass":
        print(
            f"{status} lifecycle pack={summary.get('pack_sha')} "
            f"minecraft={summary.get('minecraft')} neoforge={summary.get('neoforge')} "
            f"checks={summary.get('checks_passed')}/{summary.get('checks_total')} "
            f"evidence={summary['evidence']}"
        )
    else:
        step = f" {summary['failed_step']}" if summary.get("failed_step") else ""
        print(f"{status}{step}: {summary.get('detail', '')} evidence={summary['evidence']}")


def run_check(args: argparse.Namespace) -> int:
    result = subprocess.run(
        build_smoke_command(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    emit(summarize(read_report(args.output), result, args.output), args.format)
    return result.returncode if result.returncode in (0, 1, 2) else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Run the existing lifecycle harness for one immutable pack commit")
    check.add_argument("pack_sha", help="Full 40-character Create-Ch4os-Packwiz commit SHA")
    check.add_argument("--accept-eula", action="store_true", help="Operator has accepted the Minecraft EULA for this test server")
    check.add_argument("--output", default="mvt-results")
    check.add_argument("--image", default="itzg/minecraft-server:java21")
    check.add_argument("--heap", default="4G")
    check.add_argument("--container-memory", default="6g")
    check.add_argument("--boot-timeout", type=int, default=900)
    check.add_argument("--format", choices=("json", "text"), default="json")
    check.set_defaults(handler=run_check)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
